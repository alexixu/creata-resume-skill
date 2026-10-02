#!/usr/bin/env python3
"""Extract local resume evidence with source locators; never execute source instructions.

No network requests or document writes. Optional Poppler, macOS Vision/Swift,
Tesseract and macOS textutil are used only as local readers. Extraction does
not confirm facts or establish PDF reading order.
"""
import argparse
import functools
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
MAX_BLOCKS = 10000
MAX_PAGES = 100
MAX_OCR_PAGES = 10
MAX_COMMAND_BYTES = 20 * 1024 * 1024
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MC = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
NS = {'w': W, 'r': R}
OD = {'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
      'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
      'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'}


class ExtractionError(ValueError):
    """Unreadable, malformed or over-limit source; callers must preserve the source."""


def command(args, timeout=30):
    # Spool output to temporary files instead of buffering unbounded child output.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            result = subprocess.run(args, stdout=stdout, stderr=stderr, timeout=timeout,
                                    env={**os.environ, 'LC_ALL': 'C'})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExtractionError(f'Local reader failed or timed out: {args[0]}: {error}') from error
        if max(stdout.tell(), stderr.tell()) > MAX_COMMAND_BYTES:
            raise ExtractionError('Local reader output exceeds the 20 MiB limit')
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode('utf-8', errors='replace')
        diagnostics = stderr.read().decode('utf-8', errors='replace').strip()
        return result.returncode, output, diagnostics


def warn(result, message):
    if message not in result['warnings']:
        result['warnings'].append(message)
    result['needs_review'] = True


def add(result, unit, text, locator=None, allow_empty=False, **extra):
    if not isinstance(text, str):
        raise ExtractionError('Reader returned a non-text block')
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text and not allow_empty:
        return None
    if len(result['blocks']) >= MAX_BLOCKS or result['_chars'] + len(text) > MAX_TEXT_CHARS:
        raise ExtractionError('Extracted content exceeds 10000 blocks or 2 million characters')
    result['_chars'] += len(text)
    block = {'id': result['source']['id'] + '/' + unit, 'text': text,
             'locator': {'source': result['source']['id'], **(locator or {})}, **extra}
    result['blocks'].append(block)
    return block


def decode_text(raw):
    for bom, encoding in ((b'\xff\xfe\x00\x00', 'utf-32'), (b'\x00\x00\xfe\xff', 'utf-32'),
                          (b'\xff\xfe', 'utf-16'), (b'\xfe\xff', 'utf-16'),
                          (b'\xef\xbb\xbf', 'utf-8-sig')):
        if raw.startswith(bom):
            try:
                return raw.decode(encoding), encoding, None
            except UnicodeDecodeError as error:
                raise ExtractionError(f'Invalid {encoding} source') from error
    try:
        return raw.decode('utf-8'), 'utf-8', None
    except UnicodeDecodeError:
        for encoding in ('gb18030', 'cp1252'):
            try:
                value = raw.decode(encoding)
                return value, encoding, f'Encoding guessed as {encoding}; review non-ASCII text against the source'
            except UnicodeDecodeError:
                pass
    raise ExtractionError('Unsupported text encoding; provide UTF-8 or BOM-marked UTF-16/32')


def paragraphs(result, text, prefix, locator=None):
    lines, start, current, number = text.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1, [], 0
    for index, line in enumerate(lines + [''], 1):
        if line.strip():
            if not current:
                start = index
            current.append(line)
        elif current:
            number += 1
            add(result, f'{prefix}/paragraph-{number}', '\n'.join(current),
                {**(locator or {}), 'paragraph': number, 'line_start': start, 'line_end': index - 1})
            current = []


def archive(raw):
    try:
        package = zipfile.ZipFile(io.BytesIO(raw))
        members = package.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
            raise ExtractionError('Document ZIP exceeds 2000 members or 100 MiB expanded data')
        names = set()
        for member in members:
            if (member.filename in names or member.filename.startswith('/')
                    or '..' in Path(member.filename).parts or member.flag_bits & 1):
                raise ExtractionError('Document ZIP has duplicate/unsafe names or encrypted entries')
            names.add(member.filename)
        return package
    except (zipfile.BadZipFile, OSError) as error:
        raise ExtractionError('Damaged or invalid document ZIP') from error


def xml_part(package, name):
    try:
        info = package.getinfo(name)
        if info.file_size > MAX_XML_BYTES:
            raise ExtractionError(f'XML part {name} exceeds 20 MiB')
        raw = package.read(name)
        if re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', raw.replace(b'\x00', b''), re.I):
            raise ExtractionError(f'XML entities/DOCTYPE are unsupported in {name}')
        return ET.fromstring(raw)
    except (KeyError, ET.ParseError, zipfile.BadZipFile, RuntimeError) as error:
        raise ExtractionError(f'Missing or damaged XML part: {name}') from error


def on_off(node):
    """OOXML OnOff: an absent value is on; unknown values stay uncertain."""
    if node is None:
        return False
    value = node.get(f'{{{W}}}val')
    if value is None or value.casefold() in ('true', '1', 'on'):
        return True
    if value.casefold() in ('false', '0', 'off'):
        return False
    return None


def word_children(node):
    """Select one readable compatibility branch, rather than duplicate both."""
    if node.tag != f'{{{MC}}}AlternateContent':
        return list(node)
    choices = node.findall(f'{{{MC}}}Choice')
    for choice in choices:
        if any(item.tag in (f'{{{W}}}p', f'{{{W}}}t', f'{{{W}}}tbl') for item in choice.iter()):
            return [choice]
    fallback = node.find(f'{{{MC}}}Fallback')
    return [fallback] if fallback is not None else choices[:1]


def selected_word_nodes(node):
    yield node
    for child in word_children(node):
        yield from selected_word_nodes(child)


def word_text(node):
    if node.tag in (f'{{{W}}}del', f'{{{W}}}moveFrom', f'{{{W}}}instrText'):
        return ''
    if node.tag == f'{{{W}}}r' and on_off(node.find('w:rPr/w:vanish', NS)) is True:
        return ''
    # Floating textboxes are emitted as their own paragraph blocks by docx().
    if node.tag == f'{{{W}}}txbxContent':
        return ''
    if node.tag == f'{{{W}}}t':
        return node.text or ''
    if node.tag == f'{{{W}}}tab':
        return '\t'
    if node.tag in (f'{{{W}}}br', f'{{{W}}}cr'):
        return '\n'
    if node.tag == f'{{{W}}}noBreakHyphen':
        return '\u2011'
    return ''.join(word_text(child) for child in word_children(node))


def visible_word_nodes(node):
    """Walk displayed nodes without leaking hyperlinks from deleted runs."""
    if node.tag in (f'{{{W}}}del', f'{{{W}}}moveFrom'):
        return
    if node.tag == f'{{{W}}}r' and on_off(node.find('w:rPr/w:vanish', NS)) is True:
        return
    yield node
    if node.tag == f'{{{W}}}txbxContent':
        return
    for child in word_children(node):
        yield from visible_word_nodes(child)


def relationships(package, part):
    folder, name = posixpath.split(part)
    relfile = posixpath.join(folder, '_rels', name + '.rels')
    if relfile not in package.namelist():
        return {}
    return {item.get('Id'): item.attrib for item in xml_part(package, relfile)}


def docx(result, raw):
    with archive(raw) as package:
        types = xml_part(package, '[Content_Types].xml')
        if not any('/word/document.xml' == item.get('PartName') for item in types):
            raise ExtractionError('ZIP is not a DOCX word-processing document')
        main = xml_part(package, 'word/document.xml')
        body = main.find('w:body', NS)
        if body is None:
            raise ExtractionError('DOCX has no document body')
        mainrels = relationships(package, 'word/document.xml')
        styles, default_style = {}, None
        if 'word/styles.xml' in package.namelist():
            for style in xml_part(package, 'word/styles.xml').findall('w:style', NS):
                if style.get(f'{{{W}}}type', 'paragraph') != 'paragraph':
                    continue
                style_id = style.get(f'{{{W}}}styleId')
                if style_id:
                    if style_id in styles:
                        styles[style_id] = None
                        warn(result, f'DOCX duplicate paragraph style {style_id}; list semantics require review')
                    else:
                        styles[style_id] = style
                if style.get(f'{{{W}}}default') in ('1', 'true', 'on'):
                    default_style = style_id

        def paragraph_numbering(node):
            """Resolve only structural list intent; never manufacture visible labels."""
            direct = node.find('w:pPr/w:numPr', NS)
            pstyle = node.find('w:pPr/w:pStyle', NS)
            style_id = pstyle.get(f'{{{W}}}val') if pstyle is not None else default_style
            properties, origins, seen = {}, {}, set()
            has_numpr, unresolved = direct is not None, False
            current = style_id
            while current:
                if current in seen or len(seen) >= 128:
                    warn(result, f'DOCX paragraph style inheritance cycle/depth at {current}; list semantics require review')
                    unresolved = True
                    break
                seen.add(current)
                style = styles.get(current)
                if style is None:
                    warn(result, f'DOCX paragraph style {current} is missing/ambiguous; list semantics require review')
                    unresolved = True
                    break
                numbering = style.find('w:pPr/w:numPr', NS)
                if numbering is not None:
                    has_numpr = True
                    for field in ('numId', 'ilvl'):
                        item = numbering.find(f'w:{field}', NS)
                        if item is not None and field not in properties:
                            properties[field] = item.get(f'{{{W}}}val')
                            origins[field] = 'style:' + current
                parent = style.find('w:basedOn', NS)
                current = parent.get(f'{{{W}}}val') if parent is not None else None
            if direct is not None:
                for field in ('numId', 'ilvl'):
                    item = direct.find(f'w:{field}', NS)
                    if item is not None:
                        properties[field] = item.get(f'{{{W}}}val')
                        origins[field] = 'direct'
            num_id = properties.get('numId')
            valid_id = isinstance(num_id, str) and re.fullmatch(r'[0-9]+', num_id)
            if valid_id and not num_id.strip('0'):
                return {'numbered_paragraph': False, 'numbering_status': 'cancelled',
                        'numbering_source': origins['numId'], 'num_id': num_id}
            if valid_id:
                location = {'numbered_paragraph': True, 'numbering_status': 'list',
                            'numbering_source': origins['numId'], 'num_id': num_id}
                if 'ilvl' in properties:
                    location['numbering_level'] = properties['ilvl']
                return location
            if has_numpr or unresolved:
                warn(result, 'DOCX unresolved automatic list semantics retained as candidates; labels were not invented')
                return {'numbered_paragraph': direct is not None, 'numbering_candidate': True,
                        'numbering_status': 'unresolved', 'numbering_source': 'direct' if direct is not None else 'style'}
            return {}

        result['tables'] = []
        table_number = 0
        textbox_number = 0

        def parse_part(container, part, region):
            nonlocal table_number, textbox_number
            rels = relationships(package, part)
            paragraph_number = 0
            selected_nodes = list(selected_word_nodes(container))
            tags = {element.tag for element in selected_nodes}
            if any(f'{{{W}}}{tag}' in tags for tag in ('del', 'ins', 'moveFrom', 'moveTo')):
                warn(result, 'DOCX tracked changes present: deleted/moved-from text omitted; inserted text retained for review')
            if any(element.tag == f'{{{W}}}vanish' and on_off(element) is True for element in selected_nodes):
                warn(result, 'DOCX hidden text omitted; review the original document')
            if any(element.tag == f'{{{W}}}vanish' and on_off(element) is None for element in selected_nodes):
                warn(result, 'DOCX hidden-text flag has an unknown value; text retained for source review')
            if f'{{{MC}}}AlternateContent' in tags:
                warn(result, 'DOCX compatibility content: one readable Choice/Fallback branch selected; compare with the rendered source')

            def para(node, context=None):
                nonlocal paragraph_number, textbox_number
                paragraph_number += 1
                location = {'part': part, 'region': region, 'paragraph': paragraph_number, **(context or {})}
                links = []
                for link in visible_word_nodes(node):
                    if link.tag != f'{{{W}}}hyperlink':
                        continue
                    target = rels.get(link.get(f'{{{R}}}id'), {}).get('Target')
                    if target:
                        links.append({'text': word_text(link), 'target': target})
                location.update(paragraph_numbering(node))
                if location.get('numbered_paragraph') or location.get('numbering_candidate'):
                    warn(result, 'DOCX automatic list labels are not rendered; numbered paragraph locators retained')
                block = add(result, f'docx/{part}/p{paragraph_number}', word_text(node), location, links=links)
                anchor = paragraph_number
                for child in visible_word_nodes(node):
                    if child.tag == f'{{{W}}}txbxContent':
                        textbox_number += 1
                        warn(result, 'DOCX floating textbox placement/order requires source review; internal paragraphs retained separately')
                        walk(child, {**(context or {}), 'textbox': textbox_number, 'anchor_paragraph': anchor})
                return block

            def walk(node, context=None):
                if node.tag in (f'{{{W}}}del', f'{{{W}}}moveFrom'):
                    return
                if node.tag == f'{{{W}}}p':
                    para(node, context)
                elif node.tag == f'{{{W}}}tbl':
                    table(node, context)
                else:
                    for child in word_children(node):
                        walk(child, context)

            def table(node, parent=None):
                nonlocal table_number, paragraph_number
                table_number += 1
                number = table_number
                record = {'table': number, 'part': part, 'cells': []}
                result['tables'].append(record)
                warn(result, 'DOCX table reading order requires review; cell coordinates and merged spans are preserved separately')
                active = {}
                rows = node.findall('w:tr', NS)
                for ri, row in enumerate(rows, 1):
                    if row.find('w:trPr/w:del', NS) is not None:
                        continue
                    before = row.find('w:trPr/w:gridBefore', NS)
                    column = int(before.get(f'{{{W}}}val', '0')) + 1 if before is not None else 1
                    continued, next_active = set(), {}
                    for cell in row.findall('w:tc', NS):
                        span = cell.find('w:tcPr/w:gridSpan', NS)
                        width = int(span.get(f'{{{W}}}val', '1')) if span is not None else 1
                        if not 1 <= width <= 1000 or not 1 <= column <= 10000:
                            raise ExtractionError('DOCX table span/column exceeds limits')
                        if cell.find('w:tcPr/w:cellDel', NS) is not None:
                            warn(result, 'DOCX tracked deleted table cells omitted; original coordinates retained')
                            column += width
                            continue
                        merge = cell.find('w:tcPr/w:vMerge', NS)
                        state = merge.get(f'{{{W}}}val', 'continue') if merge is not None else None
                        metadata = {'table': number, 'row': ri, 'column': column,
                                    'column_span': width, 'row_span': 1}
                        if parent:
                            metadata['parent_cell'] = parent
                        if state == 'continue' and column in active:
                            origin = active[column]
                            metadata['merge_origin'] = [origin['row'], origin['column']]
                            if origin['column'] not in continued:
                                origin['row_span'] += 1
                                for original in origin.get('_blocks', []):
                                    original['locator']['row_span'] = origin['row_span']
                                continued.add(origin['column'])
                            for col in range(column, column + width):
                                next_active[col] = origin
                        elif state == 'restart':
                            for col in range(column, column + width):
                                next_active[col] = metadata
                        elif state == 'continue':
                            warn(result, 'DOCX merged-cell continuation has no matching origin; original cell retained')
                        start = len(result['blocks'])
                        for child in cell:
                            if child.tag != f'{{{W}}}tcPr':
                                walk(child, metadata.copy())
                        blocks = result['blocks'][start:]
                        if not blocks:
                            block = add(result, f'docx/{part}/t{number}r{ri}c{column}', '',
                                        {'part': part, 'region': region, **metadata}, allow_empty=True)
                            blocks = [block]
                        metadata['_blocks'] = blocks
                        record['cells'].append(metadata)
                        column += width
                    active = next_active
                record['rows'] = len(rows)

            walk(container)

        for region, tag in (('header', 'headerReference'),):
            seen = set()
            for reference in main.iter(f'{{{W}}}{tag}'):
                relation = mainrels.get(reference.get(f'{{{R}}}id'), {})
                target = relation.get('Target')
                if not target or relation.get('TargetMode') == 'External':
                    continue
                part = posixpath.normpath(posixpath.join('word', target.lstrip('/'))) if not target.startswith('/') else target.lstrip('/')
                if part not in seen:
                    seen.add(part)
                    parse_part(xml_part(package, part), part, region)
        parse_part(body, 'word/document.xml', 'body')
        seen = set()
        for reference in main.iter(f'{{{W}}}footerReference'):
            relation = mainrels.get(reference.get(f'{{{R}}}id'), {})
            target = relation.get('Target')
            if target and relation.get('TargetMode') != 'External':
                part = posixpath.normpath(posixpath.join('word', target)) if not target.startswith('/') else target.lstrip('/')
                if part not in seen:
                    seen.add(part)
                    parse_part(xml_part(package, part), part, 'footer')
        for table in result['tables']:
            for cell in table['cells']:
                cell['block_ids'] = [block['id'] for block in cell.pop('_blocks')]
        warn(result, 'DOCX source locators identify paragraphs/tables, not rendered page numbers; review header/body reading order')
        result['method'] = 'docx-zip-xml'


def odt_text(node):
    local = node.tag.rsplit('}', 1)[-1]
    if local in ('tracked-changes', 'deletion'):
        return ''
    if local == 's':
        count = int(node.get(f'{{{OD["text"]}}}c', '1'))
        if count > 10000:
            raise ExtractionError('ODT repeated spaces exceed limits')
        return ' ' * count
    if local in ('tab', 'line-break'):
        return '\t' if local == 'tab' else '\n'
    return (node.text or '') + ''.join(odt_text(child) + (child.tail or '') for child in node)


def odt(result, raw):
    with archive(raw) as package:
        if package.read('mimetype').strip() != b'application/vnd.oasis.opendocument.text':
            raise ExtractionError('ZIP is not an ODT text document')
        root = xml_part(package, 'content.xml')
        body = root.find('office:body/office:text', OD)
        if body is None:
            raise ExtractionError('ODT has no text body')
        counter, table_number = 0, 0

        def walk(node, context=None):
            nonlocal counter, table_number
            local = node.tag.rsplit('}', 1)[-1]
            if local in ('tracked-changes', 'deletion'):
                warn(result, 'ODT tracked/deleted text omitted; review changes in the original')
                return
            if local in ('p', 'h'):
                counter += 1
                add(result, f'odt/p{counter}', odt_text(node),
                    {'part': 'content.xml', 'paragraph': counter, **(context or {})})
                return
            if local == 'table':
                table_number += 1
                number = table_number
                warn(result, 'ODT table reading order requires review; cells and spans are kept separately')
                row_number = 0
                for row in node.iter(f'{{{OD["table"]}}}table-row'):
                    repeat = int(row.get(f'{{{OD["table"]}}}number-rows-repeated', '1'))
                    if not 1 <= repeat <= 1000:
                        raise ExtractionError('ODT repeated rows exceed limits')
                    for _ in range(repeat):
                        row_number += 1
                        column = 1
                        for cell in row:
                            kind = cell.tag.rsplit('}', 1)[-1]
                            if kind not in ('table-cell', 'covered-table-cell'):
                                continue
                            repeats = int(cell.get(f'{{{OD["table"]}}}number-columns-repeated', '1'))
                            if not 1 <= repeats <= 1000:
                                raise ExtractionError('ODT repeated columns exceed limits')
                            for _ in range(repeats):
                                location = {'table': number, 'row': row_number, 'column': column,
                                            'row_span': int(cell.get(f'{{{OD["table"]}}}number-rows-spanned', '1')),
                                            'column_span': int(cell.get(f'{{{OD["table"]}}}number-columns-spanned', '1'))}
                                if kind == 'covered-table-cell':
                                    location['merge_continuation'] = True
                                for child in cell:
                                    walk(child, location)
                                column += 1
                return
            for child in node:
                walk(child, context)

        walk(body)
        result['method'] = 'odt-zip-xml'
        warn(result, 'ODT locators identify source paragraphs/cells, not rendered pages')


class VisibleHTML(HTMLParser):
    BLOCKS = {'p', 'div', 'section', 'article', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'td', 'th', 'br'}
    SKIP = {'script', 'style', 'head', 'template', 'noscript', 'iframe', 'object'}

    def __init__(self, result):
        super().__init__(convert_charrefs=True)
        self.result, self.stack, self.buffer, self.links = result, [], [], []
        self.number, self.start, self.tags = 0, 1, 0

    def hidden(self):
        return any(flag for _, flag in self.stack)

    def flush(self):
        value = ''.join(self.buffer).strip()
        if value:
            self.number += 1
            add(self.result, f'html/block{self.number}', value,
                {'line_start': self.start, 'line_end': self.getpos()[0], 'html_block': self.number},
                links=self.links)
        self.buffer, self.links = [], []

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        attributes = dict(attrs)
        hidden = tag in self.SKIP or 'hidden' in attributes or attributes.get('aria-hidden') == 'true'
        hidden = hidden or bool(re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', attributes.get('style', ''), re.I))
        if tag in self.BLOCKS and not self.hidden():
            self.flush()
        if tag not in ('br', 'img', 'hr', 'meta', 'link', 'input', 'source', 'wbr'):
            self.stack.append((tag, hidden))
        if not self.hidden() and not hidden and tag == 'a' and attributes.get('href'):
            href = attributes['href']
            if re.match(r'\s*(?:javascript|data|vbscript):', href, re.I):
                warn(self.result, 'Executable HTML link target omitted; its visible text is retained')
            else:
                self.links.append({'target': href})
        if tag == 'br' and not self.hidden():
            self.flush()

    def handle_endtag(self, tag):
        if tag in self.BLOCKS and not self.hidden():
            self.flush()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        if not self.hidden():
            if not self.buffer:
                self.start = self.getpos()[0]
            self.buffer.append(value)


@functools.lru_cache(maxsize=1)
def ocr_capabilities():
    capability = {'engine': None, 'languages': [], 'warnings': []}
    swift = shutil.which('swift') if sys.platform == 'darwin' else None
    if swift and (ROOT / 'ocr_resume.swift').is_file():
        try:
            code, output, error = command([swift, str(ROOT / 'ocr_resume.swift'), '--probe'], timeout=90)
            if code == 0:
                data = json.loads(output)
                return {'engine': 'vision', 'command': swift, 'languages': data['languages'], 'warnings': []}
            capability['warnings'].append('Swift Vision probe failed: ' + error[:300])
        except (ExtractionError, ValueError, KeyError) as error:
            capability['warnings'].append(str(error))
    tesseract = shutil.which('tesseract')
    if tesseract:
        code, output, error = command([tesseract, '--list-langs'])
        available = output.splitlines()[1:] if code == 0 else []
        languages = [name for name in ('eng', 'chi_sim', 'chi_tra') if name in available]
        if languages:
            return {'engine': 'tesseract', 'command': tesseract, 'languages': languages, 'warnings': capability['warnings']}
        capability['warnings'].append('Tesseract has no supported English/Chinese language data')
    return capability


def recognize(images):
    capability = ocr_capabilities()
    if capability['engine'] is None:
        return capability, [{'blocks': [], 'error': 'Local OCR unavailable: requires macOS Swift/Vision or Tesseract language data'} for _ in images]
    if capability['engine'] == 'vision':
        code, output, error = command([capability['command'], str(ROOT / 'ocr_resume.swift'), *map(str, images)], timeout=120)
        if code:
            return capability, [{'blocks': [], 'error': error[:1000]} for _ in images]
        try:
            records = json.loads(output)['images']
            if len(records) != len(images):
                raise ValueError('Unexpected number of OCR image results')
            return capability, records
        except (ValueError, KeyError, TypeError) as error:
            raise ExtractionError('Invalid local OCR result') from error
    records = []
    for image in images:
        code, output, error = command([capability['command'], str(image), 'stdout', '-l',
                                      '+'.join(capability['languages']), 'tsv'], timeout=30)
        groups = {}
        if not code:
            for line in output.splitlines()[1:]:
                fields = line.split('\t', 11)
                if len(fields) == 12 and fields[0] == '5' and fields[11].strip():
                    key = tuple(fields[1:5])
                    item = groups.setdefault(key, {'words': [], 'confidence': []})
                    item['words'].append(fields[11])
                    item['confidence'].append(float(fields[10]) / 100)
        records.append({'blocks': [{'text': ' '.join(item['words']),
                                     'confidence': min(item['confidence'])} for item in groups.values()],
                        **({'error': error[:1000]} if code else {})})
    return capability, records


def ocr_blocks(result, images, pages):
    capability, records = recognize(images)
    result['ocr'] = {'engine': capability['engine'], 'languages': capability['languages']}
    for message in capability['warnings']:
        warn(result, message)
    for page, record in zip(pages, records):
        count = 0
        for index, item in enumerate(record.get('blocks', []), 1):
            text = item.get('text', '').strip()
            if not text:
                continue
            count += 1
            confidence = float(item.get('confidence', 0))
            add(result, f'ocr/page{page}/line{index}', text,
                {'page': page, 'ocr_line': index, 'ocr_engine': capability['engine'],
                 'languages': capability['languages'], **({'bbox': item['bbox']} if 'bbox' in item else {})},
                confidence=confidence)
            if confidence < 0.75:
                warn(result, f'Page {page}: OCR low confidence; verify names, dates, numbers and punctuation')
        page_record = next((item for item in result['pages'] if item['page'] == page), None)
        if page_record is not None:
            page_record['method'] = capability['engine'] or 'unavailable'
            page_record['status'] = 'ocr' if count else 'unreadable_or_blank'
        if not count:
            result['status'] = 'partial' if result['blocks'] else 'needs_dependency' if not capability['engine'] else 'partial'
            warn(result, f'Page {page}: OCR produced no text; page may be blank or unreadable. ' + record.get('error', ''))
        else:
            result.setdefault('ocr_pages', []).append(page)
    if capability['engine']:
        result['method'] += '+' + capability['engine']
    warn(result, 'OCR text and reading order require comparison with the source; OCR does not confirm resume facts')


def image_dimensions(raw, suffix):
    more_pages = False
    if suffix == '.png':
        if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw) < 24:
            raise ExtractionError('Invalid PNG signature/header')
        width, height = struct.unpack('>II', raw[16:24])
    elif suffix in ('.jpg', '.jpeg'):
        if not raw.startswith(b'\xff\xd8'):
            raise ExtractionError('Invalid JPEG signature')
        position, width, height = 2, 0, 0
        while position + 4 <= len(raw):
            if raw[position] != 255:
                position += 1
                continue
            marker = raw[position + 1]
            position += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(raw[position:position + 2], 'big')
            if length < 2:
                raise ExtractionError('Damaged JPEG segment')
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack('>HH', raw[position + 3:position + 7])
                break
            position += length
    else:
        if raw[:4] not in (b'II*\x00', b'MM\x00*'):
            raise ExtractionError('Invalid or unsupported TIFF header (classic TIFF required)')
        order = '<' if raw[:2] == b'II' else '>'
        offset = struct.unpack(order + 'I', raw[4:8])[0]
        if offset + 2 > len(raw):
            raise ExtractionError('Damaged TIFF directory')
        count = struct.unpack(order + 'H', raw[offset:offset + 2])[0]
        values = {}
        for index in range(count):
            entry = offset + 2 + index * 12
            if entry + 12 > len(raw):
                raise ExtractionError('Damaged TIFF tags')
            tag, kind, number = struct.unpack(order + 'HHI', raw[entry:entry + 8])
            if tag in (256, 257) and number == 1 and kind in (3, 4):
                values[tag] = struct.unpack(order + ('H' if kind == 3 else 'I'), raw[entry + 8:entry + (10 if kind == 3 else 12)])[0]
        width, height = values.get(256, 0), values.get(257, 0)
        next_offset = offset + 2 + count * 12
        more_pages = next_offset + 4 <= len(raw) and struct.unpack(order + 'I', raw[next_offset:next_offset + 4])[0] != 0
    if not width or not height or width > 10000 or height > 10000 or width * height > 40_000_000:
        raise ExtractionError('Image dimensions invalid or exceed 40 million pixels / 10000 pixels per side')
    return width, height, more_pages


def possible_columns(value):
    # Right-aligned dates are a conventional single-column resume entry header.
    date = r'(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+)?(?:\d{4}|YYYY)(?:[./年-](?:\d{1,2}|MM))?(?:月)?'
    dates = re.compile(rf'{date}\s*(?:[–—~至-]\s*(?:{date}|Present|Current|Now|至今|现在))?', re.I)
    for line in value.splitlines():
        pieces = re.split(r'\s{3,}', line.strip())
        if len(pieces) > 1 and not dates.fullmatch(pieces[-1]):
            return True
    return False


def image_backed_pdf_pages(path, info, result):
    """Detect large raster content so a text footer cannot conceal scanned body text."""
    if not shutil.which('pdfimages'):
        warn(result, 'pdfimages unavailable: mixed text/image PDF coverage could not be checked; inspect source for scanned body content')
        return set()
    code, listing, error = command(['pdfimages', '-list', str(path)])
    if code:
        warn(result, 'PDF image coverage check failed; inspect mixed/scanned body content: ' + error[:300])
        return set()
    size = re.search(r'^Page size:\s+([\d.]+) x ([\d.]+)', info, re.M)
    page_area = float(size[1]) * float(size[2]) if size else 595 * 842
    pages = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) < 14 or not fields[0].isdigit() or fields[2] != 'image':
            continue
        try:
            width, height, xppi, yppi = map(float, (fields[3], fields[4], fields[12], fields[13]))
            if xppi > 0 and yppi > 0 and (width * 72 / xppi) * (height * 72 / yppi) >= page_area * 0.3:
                pages.add(int(fields[0]))
        except ValueError:
            warn(result, 'PDF image coverage listing could not be fully interpreted; inspect source images')
    return pages


def pdf(result, path, raw):
    if b'%PDF-' not in raw[:1024]:
        raise ExtractionError('File does not have a PDF header')
    missing = [name for name in ('pdfinfo', 'pdftotext') if not shutil.which(name)]
    if missing:
        result['status'] = 'needs_dependency'
        warn(result, 'PDF extraction requires local Poppler tools: ' + ', '.join(missing))
        return
    code, info, error = command(['pdfinfo', str(path)])
    if 'incorrect password' in error.lower() or re.search(r'^Encrypted:\s+yes', info, re.M):
        result['status'] = 'error'
        warn(result, 'Encrypted/password-protected PDF: provide a locally decrypted copy; no password was attempted')
        return
    if code:
        raise ExtractionError('Cannot read PDF: ' + error[:1000])
    pages = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not pages:
        raise ExtractionError('pdfinfo did not report a page count')
    count = int(pages[1])
    if not 1 <= count <= MAX_PAGES:
        raise ExtractionError(f'PDF has {count} pages; limit is {MAX_PAGES}')
    result['page_count'] = count
    code, text, error = command(['pdftotext', '-layout', '-enc', 'UTF-8', str(path), '-'])
    if code:
        raise ExtractionError('PDF text extraction failed: ' + error[:1000])
    if error:
        warn(result, 'Poppler text/layout diagnostic: ' + error[:1000])
    chunks = text.split('\f')
    if chunks and not chunks[-1].strip():
        chunks.pop()
    if len(chunks) != count:
        warn(result, 'PDF page separators differ from pdfinfo; source page boundaries require review')
    image_pages = image_backed_pdf_pages(path, info, result)
    result['pages'], scan_pages = [], []
    for page in range(1, count + 1):
        value = chunks[page - 1] if page <= len(chunks) else ''
        result['pages'].append({'page': page, 'status': 'text' if value.strip() else 'no_text', 'method': 'poppler-layout'})
        if value.strip():
            paragraphs(result, value, f'pdf/page{page}', {'page': page})
            if possible_columns(value):
                warn(result, f'Page {page}: possible columns/table layout; verify reading order before combining blocks')
        # A few footer/name characters do not establish coverage of a scanned page.
        sparse = len(re.sub(r'\W+', '', value)) < 30
        if not value.strip() or sparse or page in image_pages:
            scan_pages.append(page)
            if value.strip():
                result['pages'][-1]['status'] = 'mixed_or_sparse'
                warn(result, f'Page {page}: mixed image/text or sparse text detected; original text and OCR evidence are separate and may duplicate content; review coverage')
    result['method'] = 'poppler-layout'
    warn(result, 'PDF layout extraction does not establish semantic reading order; compare with rendered source pages')
    if scan_pages:
        if not shutil.which('pdftoppm'):
            result['status'] = 'partial' if result['blocks'] else 'needs_dependency'
            warn(result, 'Image-only/blank or mixed PDF pages require local pdftoppm and OCR: ' + ', '.join(map(str, scan_pages)))
            return
        with tempfile.TemporaryDirectory(prefix='resume-ocr-') as directory:
            images, selected = [], scan_pages[:MAX_OCR_PAGES]
            for page in selected:
                stem = Path(directory) / f'page-{page}'
                code, _, error = command(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile',
                                          '-scale-to', '2200', '-png', str(path), str(stem)])
                if code:
                    raise ExtractionError(f'Cannot rasterize PDF page {page}: {error[:1000]}')
                images.append(stem.with_suffix('.png'))
            ocr_blocks(result, images, selected)
        result['blocks'].sort(key=lambda block: block['locator'].get('page', 0))
        if len(scan_pages) > MAX_OCR_PAGES:
            result['status'] = 'partial'
            warn(result, 'OCR page limit reached; unprocessed pages: ' + ', '.join(map(str, scan_pages[MAX_OCR_PAGES:])))


def json_source(result, raw):
    text, encoding, warning = decode_text(raw)
    if warning:
        warn(result, warning)
    result['encoding'] = encoding
    def pairs(values):
        data = {}
        for key, value in values:
            if key in data:
                raise ExtractionError(f'JSON has duplicate key: {key}')
            data[key] = value
        return data
    try:
        data = json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number: ' + value)))
    except (ValueError, RecursionError) as error:
        raise ExtractionError('Invalid JSON source: ' + str(error)) from error
    spec = importlib.util.spec_from_file_location('resume_extract_renderer', ROOT / 'resume.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    try:
        renderer.validate(data, draft=True)
    except (ValueError, TypeError) as error:
        result['method'] = 'json-generic'
        warn(result, 'JSON is not a validated renderer resume: ' + str(error))
        def leaves(value, location='$', depth=0):
            if depth > 100:
                raise ExtractionError('JSON nesting exceeds 100 levels')
            if isinstance(value, dict):
                for key, item in value.items():
                    leaves(item, location + '[' + json.dumps(key, ensure_ascii=False) + ']', depth + 1)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    leaves(item, f'{location}[{index}]', depth + 1)
            elif value is not None:
                add(result, 'json/' + location, str(value), {'json_path': location})
        leaves(data)
    else:
        result['method'], result['structured_resume'] = 'renderer-json', data
        result['source_metadata'] = {key: data[key] for key in ('role', 'facts_confirmed', 'example_notice') if key in data}
        for location, value in renderer.iter_visible_fields(data):
            add(result, 'json/' + location, value, {'json_path': location})


def extract(path):
    """Return evidence blocks, never a confirmed resume. Does not mutate the source.

    Corrupt/oversized input raises ExtractionError. Missing optional local tools,
    encrypted PDFs and empty OCR return explicit status/warnings and no invented
    text. Block locators contain an opaque source ID rather than private paths.
    """
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ExtractionError('Source must be an existing local regular file')
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ExtractionError('Source exceeds the 10 MiB size limit')
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise ExtractionError('Source grew beyond the 10 MiB size limit')
    digest, suffix = hashlib.sha256(raw).hexdigest(), path.suffix.lower()
    formats = {'.txt': 'text', '.md': 'markdown', '.markdown': 'markdown', '.html': 'html', '.htm': 'html',
               '.pdf': 'pdf', '.docx': 'docx', '.odt': 'odt', '.rtf': 'rtf', '.doc': 'doc',
               '.json': 'json', '.png': 'png', '.jpg': 'jpeg', '.jpeg': 'jpeg', '.tif': 'tiff', '.tiff': 'tiff'}
    result = {'format': formats.get(suffix, 'unknown'), 'method': 'none', 'status': 'extracted',
              'source': {'id': 'src-' + digest[:16], 'filename': path.name, 'sha256': digest,
                         'size': len(raw), 'absolute_path': str(path)},
              'blocks': [], 'warnings': [], 'needs_review': False, 'untrusted_source': True, '_chars': 0}
    if not raw:
        raise ExtractionError('Source is empty')
    try:
        if suffix == '.pdf':
            pdf(result, path, raw)
        elif suffix == '.docx':
            docx(result, raw)
        elif suffix == '.odt':
            odt(result, raw)
        elif suffix == '.json':
            json_source(result, raw)
        elif suffix in ('.txt', '.md', '.markdown', '.html', '.htm'):
            value, result['encoding'], warning = decode_text(raw)
            if warning:
                warn(result, warning)
            if any(ord(char) < 32 and char not in '\n\r\t\f' for char in value):
                raise ExtractionError('Text source contains binary/control bytes; encoding or format requires correction')
            if suffix in ('.html', '.htm'):
                reader = VisibleHTML(result)
                reader.feed(value)
                reader.close()
                reader.flush()
                if not reader.tags:
                    raise ExtractionError('HTML source contains no HTML elements')
                result['method'] = 'html-visible-text'
                warn(result, 'HTML scripts/styles/hidden elements were omitted; external CSS and resources were not loaded')
            else:
                result['method'] = 'markdown-source' if result['format'] == 'markdown' else 'text-decoded'
                paragraphs(result, value, result['format'])
        elif suffix in ('.rtf', '.doc'):
            if suffix == '.rtf' and not raw.lstrip().startswith(b'{\\rtf'):
                raise ExtractionError('Invalid RTF signature')
            if suffix == '.doc' and not raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
                raise ExtractionError('Invalid legacy Word DOC/OLE signature')
            if not shutil.which('textutil'):
                result['status'] = 'needs_dependency'
                warn(result, 'RTF/DOC requires macOS textutil; convert locally to DOCX or UTF-8 text on other systems')
            else:
                code, value, error = command(['textutil', '-convert', 'txt', '-format', suffix[1:], '-stdout',
                                              '-encoding', 'UTF-8', '-noload', '-nostore', '--', str(path)])
                if code:
                    raise ExtractionError('Local textutil conversion failed: ' + error[:1000])
                result['method'] = 'textutil-local'
                paragraphs(result, value, 'converted', {'converted': True})
                warn(result, 'Local conversion flattens layout; locators refer to converted paragraphs, not original pages/cells')
        elif suffix in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
            width, height, multi = image_dimensions(raw, suffix)
            result['image_dimensions'] = {'width': width, 'height': height}
            result['method'], result['pages'] = 'image-ocr', [{'page': 1, 'status': 'no_text', 'method': 'none'}]
            ocr_blocks(result, [path], [1])
            if multi:
                result['status'] = 'partial'
                warn(result, 'Multi-page TIFF detected: only its first image was OCRed; convert all pages to PDF for full extraction')
        else:
            result['status'] = 'unsupported'
            warn(result, 'Unsupported extension; provide PDF, DOCX, ODT, RTF, DOC, text, Markdown, HTML, renderer JSON or PNG/JPEG/TIFF')
    except ExtractionError:
        raise
    except (ET.ParseError, zipfile.BadZipFile, KeyError, struct.error, OverflowError, ValueError, RecursionError) as error:
        raise ExtractionError('Damaged or malformed source: ' + str(error)) from error
    if not any(block['text'].strip() for block in result['blocks']):
        if result['status'] == 'extracted':
            result['status'] = 'partial'
        warn(result, 'No body text was extracted; do not create resume claims from this result')
    if '\ufffd' in ''.join(block['text'] for block in result['blocks']):
        warn(result, 'Replacement characters detected; source encoding/font text requires review')
    result.pop('_chars')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(extract(args.input), ensure_ascii=False, indent=2))
    except (ExtractionError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
