#!/usr/bin/env python3
"""Import local historical resumes into a traceable, unconfirmed editable draft."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_SOURCES = 10
MAX_BLOCKS = 10000
MAX_TEXT_CHARS = 2000000


def local_module(name):
    path = ROOT / 'scripts' / f'{name}.py'
    if not path.is_file():
        raise ValueError(f'{path.name} is required for resume import')
    spec = importlib.util.spec_from_file_location(f'import_{name}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = local_module('resume')
ALIASES = {
    'summary': ('summary', 'profile', 'professional summary', '个人概览', '个人简介', '职业概览', '自我评价'),
    'experience': ('experience', 'work experience', 'professional experience', 'employment history',
                   '工作经历', '工作经验', '职业经历', '实习经历'),
    'projects': ('projects', 'selected projects', 'project experience', '项目经历', '项目经验', '精选项目'),
    'education': ('education', 'education background', 'academic background', '教育背景', '教育经历', '学历'),
    'skills': ('skills', 'technical skills', 'professional skills', '专业能力', '专业技能', '技能'),
    'certifications': ('certifications', 'certificates', '资格证书', '证书', '专业资格'),
}
SECTION_LOOKUP = {label: key for key, labels in ALIASES.items() for label in labels}
EMAIL = re.compile(r'\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b', re.I)
URL = re.compile(r'\b(?:https?://|www\.)\S+|\b(?:github\.com|linkedin\.com)/\S+|'
                 r'\b[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}/\S+', re.I)
PHONE = re.compile(r'(?<!\d)(?:\+\d{1,3}[ .\-]?)?(?:\(?\d{3}\)?[ .\-]?)?\d{3}[ .\-]\d{4}(?!\d)|(?<!\d)1[3-9]\d{9}(?!\d)')
NAME_LABEL = re.compile(r'^(?:name|姓名)\s*[:：]\s*(.+)$', re.I)
DATE_ATOM = (r'(?:(?:19|20)\d{2}(?:[./\-]\d{1,2}|年\d{1,2}月)?年?|'
             r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
             r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
             r'\.?\s+(?:19|20)\d{2})')
DATE_SPAN = re.compile(DATE_ATOM + r'\s*(?:[-–—~至到]|\bto\b)\s*(?:' + DATE_ATOM + r'|present|now|至今)', re.I)
BULLET = re.compile(r'^\s*(?:-(?!\d|\.\d|[$€£¥]\s*(?:\d|\.\d))\s*|[*•●▪◦‣]\s*|\d+[.)、]\s+)(.+)$', re.S)
DATE_SECTIONS = {'experience', 'projects', 'education'}
DATED_ACTION = re.compile(r'^(?:supported|built|maintained|implemented|created|contributed|led|wrote|developed|'
                          r'delivered|coordinated|documented|managed)\b', re.I)


def heading_key(value):
    value = value.strip()
    while value and (unicodedata.category(value[0])[0] in 'PSZ' or value[0].isspace() or '\ue000' <= value[0] <= '\uf8ff'):
        value = value[1:]
    while value and (unicodedata.category(value[-1])[0] in 'PSZ' or value[-1].isspace()):
        value = value[:-1]
    value = re.sub(r'^\d+[.)、]\s*', '', value)
    return SECTION_LOOKUP.get(' '.join(value.split()).casefold())


def candidate_name(value):
    value = value.strip().strip('#* ').strip()
    label = NAME_LABEL.fullmatch(value.strip())
    if label:
        result = label[1].strip()
        return result if len(result) <= 100 else None
    value = value.strip()
    if heading_key(value) or extra_heading(value) or EMAIL.search(value) or URL.search(value) or PHONE.search(value):
        return None
    if re.search(r'resume|curriculum|vitae|engineer|manager|developer|designer|analyst|candidate profile|'
                 r'求职|简历|经理|工程师|设计师|毕业|大学|公司|学院|产品|运营|研究员', value, re.I):
        return None
    if re.fullmatch(r'[\u4e00-\u9fff]{2,6}', value):
        return value
    words = value.split()
    if 2 <= len(words) <= 4 and all(re.fullmatch(r'[A-Z][A-Za-z’\'\-]*', word) for word in words):
        return value
    return None


def contact_candidate(value):
    return len(value) <= 180 and not '\n' in value and bool(EMAIL.search(value) or URL.search(value) or PHONE.search(value))


def extra_heading(value):
    return bool(re.fullmatch(r'(?:Other |Additional )?(?:Awards(?:\s+(?:and|&)\s+Honou?rs)?|'
                             r'Honou?rs(?:\s+(?:and|&)\s+Awards)?|Publications|Interests|Languages|References|'
                             r'Activities|Notes|Original Notes|Volunteer Activities)', value, re.I) or
                value in ('其他说明', '其他信息', '获奖经历', '兴趣爱好', '志愿经历', '学术成果', '发表论文'))


def unknown_heading(value):
    """Recognize explicit extra headings without assigning occupational meaning."""
    return bool(re.fullmatch(r'#+\s+[^\n]{1,80}', value) or extra_heading(value))


def same_flow(left, right):
    return all(left['locator'].get(key) == right['locator'].get(key)
               for key in ('part', 'region', 'page', 'column', 'table', 'textbox'))


def automatic_list(item):
    locator = item['locator']
    return bool(locator.get('numbered_paragraph') or locator.get('numbering_candidate') or locator.get('list_item'))


def date_header(value):
    # A dated action sentence is body copy, even if an old document lost its
    # list styling. Explicit date spans alone are not proof of an entry header.
    match = DATE_SPAN.search(value)
    if (match and DATED_ACTION.match(value)
            and re.search(r'\b(?:from|during|between|in|over)\s*$', value[:match.start()], re.I)):
        return None
    return match


def language_choice(text):
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
    letters = len(re.findall(r'[A-Za-z]', text))
    return 'zh' if cjk and cjk >= letters / 3 else 'en'


def safe_output(inputs, output):
    raw = Path(output).expanduser()
    if '..' in raw.parts or raw.is_symlink():
        raise ValueError('--out: parent traversal and symlink output directories are not allowed')
    out = raw.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError('--out: resume imports belong outside the public skill repository')
    if any(out == source or out in source.parents for source in inputs):
        raise ValueError('--out must not overwrite an input or contain any input file')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('--out must be a new or empty private directory; previous drafts are preserved')
    return out


def segments(source):
    for block in source['extraction']['blocks']:
        lines = block['text'].splitlines(keepends=True)
        links = [copy.deepcopy(link) for link in block.get('links', [])
                 if isinstance(link, dict) and isinstance(link.get('target'), str)]
        offset = 0
        for index, raw_line in enumerate(lines):
            line = raw_line.rstrip('\r\n')
            line_start, line_end = offset, offset + len(line)
            offset += len(raw_line)
            if not line.strip():
                continue
            line_links = []
            for link in links:
                start, end = link.get('text_start'), link.get('text_end')
                if (type(start) is int and type(end) is int
                        and 0 <= start < end <= len(block['text'])):
                    if start < line_end and end > line_start:
                        local = copy.deepcopy(link)
                        local['text'] = line[max(0, start-line_start):min(len(line), end-line_start)]
                        line_links.append(local)
                elif (len(lines) == 1 or isinstance(link.get('text'), str)
                      and link['text'].strip() and link['text'].strip() in line):
                    line_links.append(link)
            yield {'text': line.strip(), 'raw_text': line,
                   'indent': len(line.expandtabs(8)) - len(line.expandtabs(8).lstrip(' ')),
                   'block_id': block['id'], 'locator': {
                **block['locator'], 'line_in_block': index + 1}, 'source_id': source['id'],
                'sha256': source['sha256'], 'links': links, 'line_links': line_links}


def linked_contacts(item):
    """Expose explicit header link targets as unconfirmed contact candidates."""
    contacts = []
    for link in item.get('line_links', []):
        target = link['target'].strip()
        if not target or len(target) > 180 or any(unicodedata.category(c) == 'Cc' for c in target):
            continue
        try:
            parsed = urlsplit(target)
            if parsed.scheme.lower() == 'mailto':
                candidate = unquote(parsed.path)
                if not EMAIL.fullmatch(candidate):
                    continue
            elif parsed.scheme.lower() in ('http', 'https') and parsed.hostname:
                candidate = target
            else:
                continue
        except ValueError:
            continue
        if candidate not in contacts:
            contacts.append(candidate)
    return contacts


def nonlink_text(item):
    value = item['text']
    for link in item.get('line_links', []):
        if not linked_contacts({'line_links': [link]}):
            continue
        label = link.get('text')
        if isinstance(label, str) and label.strip():
            value = value.replace(label.strip(), '', 1)
    return value.strip(' \t|·•,;/：:–—-')


def layout_risk(source):
    warnings = ' '.join(str(warning) for warning in source['extraction'].get('warnings', []))
    # The routine statement "does not establish semantic reading order" is
    # an evidence boundary, not a diagnosis that a single-column page is mixed.
    return bool(re.search(r'multi.?column|possible\s+(?:multiple\s+)?columns?|(?:two|multiple)\s+columns|'
                          r'columns?\s+(?:layout|ambiguity|risk)|reading[ -]?order\s+(?:is\s+)?(?:ambigu|uncertain|risk)|'
                          r'ambiguous\s+reading[ -]?order|多列|双栏|阅读顺序(?:有|存在)?(?:歧义|风险)|顺序歧义',
                          warnings, re.I))


def new_profile(language):
    return {'language': language, 'name': '', 'headline': '', 'contacts': [],
            'facts_confirmed': False, 'sections': []}


def claim(item, path=None, interpretation='original_bullet'):
    record = {'text': item.get('raw_text', item['text']), 'source': {'id': item['source_id'], 'block_id': item['block_id'],
            'locator': item['locator'], 'sha256': item['sha256']},
            'resume_paths': [path] if path else [], 'status': 'source_only',
            'interpretation': interpretation, 'needs_review': True}
    if item.get('links'):
        record['source']['links'] = copy.deepcopy(item['links'])
    return record


def organize_text(source, language):
    data, facts, remaining = new_profile(language), [], []
    lines = list(segments(source))
    ambiguous = layout_risk(source)
    current = None
    unclassified = None
    last_bullet = None

    def section(key, title):
        nonlocal current
        counts = sum(s['id'] == key or s['id'].startswith(key + '-') for s in data['sections'])
        data['sections'].append({'id': key if counts == 0 else f'{key}-{counts + 1}',
                                 'title': title, 'entries': []})
        current = len(data['sections']) - 1
        return current

    def keep(item, section_index, interpretation='original_bullet', value=None, record=None):
        entries = data['sections'][section_index]['entries']
        if not entries:
            entries.append({'heading': '', 'date': '', 'bullets': []})
        entry = entries[-1]
        entry.setdefault('bullets', []).append(item['text'] if value is None else value)
        path = f'sections[{section_index}].entries[{len(entries)-1}].bullets[{len(entry["bullets"])-1}]'
        if record is None:
            facts.append(claim(item, path, interpretation))
        else:
            record['resume_paths'].append(path)
        if interpretation == 'unclassified':
            remaining.append({'claim_index': len(facts) - 1, 'reason': 'layout_ambiguity'
                              if ambiguous or 'table' in item['locator'] else 'not_classified'})

    def unclassified_keep(item, record=None):
        nonlocal unclassified, current
        if unclassified is None:
            old = current
            unclassified = section('unclassified', '原文待归类' if language == 'zh' else 'Source Content to Classify')
            current = old
        keep(item, unclassified, 'unclassified', record=record)

    index = 0
    while index < len(lines):
        item = lines[index]
        value = item['text']
        list_content = automatic_list(item)
        key = None if list_content else heading_key(value)
        if key:
            last_bullet = None
            section_index = section(key, renderer.TITLES[key][language == 'en'])
            facts.append(claim(item, f'sections[{section_index}].title', 'section_candidate'))
            index += 1
            continue
        named = candidate_name(value) if not list_content and (index == 0 or NAME_LABEL.fullmatch(value)) else None
        if named and not data['name']:
            last_bullet = None
            data['name'] = named
            facts.append(claim(item, 'name', 'name_candidate'))
            index += 1
            continue
        if not list_content and unknown_heading(value):
            last_bullet = None
            section_index = section('unclassified', value.strip('# ').strip())
            facts.append(claim(item, f'sections[{section_index}].title', 'unsupported_section_candidate'))
            remaining.append({'claim_index': len(facts) - 1, 'reason': 'unsupported_section_heading'})
            index += 1
            continue
        targets = linked_contacts(item) if current is None and not list_content else []
        if current is None and not list_content and (contact_candidate(value) or targets):
            last_bullet = None
            contacts = [value] if contact_candidate(value) else []
            visible_targets = {match.group().rstrip('),;|') for pattern in (EMAIL, URL)
                               for match in pattern.finditer(value)}
            contacts.extend(target for target in targets if target not in visible_targets)
            record = claim(item, interpretation='linked_contact_candidate' if targets else 'contact_candidate')
            for contact in contacts:
                if contact not in data['contacts']:
                    data['contacts'].append(contact)
                record['resume_paths'].append(f'contacts[{data["contacts"].index(contact)}]')
            facts.append(record)
            if targets and not contact_candidate(value) and nonlink_text(item):
                # A contact link does not account for other visible text on the
                # same line. Keep that complete line for editorial review while
                # mapping its single source record to both output locations.
                unclassified_keep(item, record=record)
            index += 1
            continue
        if current is None or ambiguous or 'table' in item['locator']:
            last_bullet = None
            unclassified_keep(item)
            index += 1
            continue
        entries = data['sections'][current]['entries']
        base_key = data['sections'][current]['id'].split('-')[0]
        # Native list markers are layout, not part of the extracted body. A
        # leading sign or symbol in that body must remain literal source text.
        is_bullet = None if list_content else BULLET.match(value)
        new_list_item = bool(is_bullet or list_content and item['locator']['line_in_block'] == 1)
        same_word_item = (list_content and last_bullet and automatic_list(last_bullet['anchor'])
                          and item['locator']['line_in_block'] > 1)
        if (last_bullet and not new_list_item and (same_word_item or not DATE_SPAN.search(value))
                and item['block_id'] == last_bullet['anchor']['block_id']
                and same_flow(item, last_bullet['anchor'])
                and (same_word_item or item['indent'] > last_bullet['anchor']['indent'])):
            entry = data['sections'][last_bullet['section']]['entries'][last_bullet['entry']]
            previous = entry['bullets'][last_bullet['bullet']]
            # Preserve any printed hyphen; its linguistic meaning requires review.
            separator = '' if previous.endswith('-') else ' '
            entry['bullets'][last_bullet['bullet']] += separator + value
            facts.append(claim(item, last_bullet['path'], 'continuation_hyphen_candidate'
                               if previous.endswith('-') else 'continuation_candidate'))
            index += 1
            continue
        match = date_header(value) if base_key in DATE_SECTIONS and not new_list_item and not list_content and len(value) <= 220 else None
        # Single-column dated headers can span one or two adjacent short lines.
        following = []
        if base_key in DATE_SECTIONS and not new_list_item and not list_content and not match and len(value) <= 140:
            for offset in (1, 2):
                if index + offset >= len(lines):
                    break
                nxt = lines[index + offset]
                if (not same_flow(item, nxt) or automatic_list(nxt) or heading_key(nxt['text']) or unknown_heading(nxt['text'])
                        or BULLET.match(nxt['text']) or len(nxt['text']) > 140):
                    break
                nxt_match = date_header(nxt['text'])
                following.append(nxt)
                if nxt_match:
                    match = nxt_match
                    break
            else:
                following = []
            if not following or not date_header(following[-1]['text']):
                following = []
        if match:
            last_bullet = None
            header_items = [item] + following
            date_item = header_items[-1] if following else item
            date_match = DATE_SPAN.search(date_item['text'])
            date_value = date_match.group()
            headings = []
            for header in header_items:
                raw = DATE_SPAN.sub('', header['text']).strip(' |/–—-\t')
                if raw:
                    headings.append(raw)
            # A date-only line followed by a short company/role line is explicit
            # adjacency, not a claim that the date or employer is verified.
            if not headings and index + 1 < len(lines):
                nxt = lines[index + 1]
                if (same_flow(item, nxt) and not automatic_list(nxt) and not heading_key(nxt['text']) and not unknown_heading(nxt['text'])
                        and not BULLET.match(nxt['text']) and len(nxt['text']) <= 140):
                    header_items.append(nxt)
                    headings.append(nxt['text'])
            if not headings:
                unclassified_keep(item)
                index += 1
                continue
            entry = {'heading': ' / '.join(headings), 'date': date_value, 'bullets': []}
            entries.append(entry)
            ep = f'sections[{current}].entries[{len(entries)-1}]'
            for header in header_items:
                paths = []
                if DATE_SPAN.search(header['text']):
                    paths.append(ep + '.date')
                if DATE_SPAN.sub('', header['text']).strip(' |/–—-\t'):
                    paths.append(ep + '.heading')
                record = claim(header, interpretation='date_candidate' if DATE_SPAN.search(header['text']) else 'entry_heading_candidate')
                record['resume_paths'] = paths
                facts.append(record)
            index += len(header_items)
            continue
        possible_continuation = (last_bullet and not new_list_item and
                                 (item['indent'] > last_bullet['anchor']['indent'] or
                                  len(value) < 50 and value[:1].islower()))
        interpretation = ('unclassified' if base_key == 'unclassified' else
                          'continuation_unmerged_candidate' if possible_continuation else 'original_bullet')
        keep(item, current, interpretation=interpretation,
             value=is_bullet[1].strip() if is_bullet else value)
        if new_list_item and base_key != 'unclassified':
            last_bullet = {'anchor': item, 'path': facts[-1]['resume_paths'][0], 'section': current,
                           'entry': len(entries) - 1, 'bullet': len(entries[-1]['bullets']) - 1}
        else:
            last_bullet = None
        index += 1
    if not data['sections']:
        section('unclassified', '原文待归类' if language == 'zh' else 'Source Content to Classify')
    return data, facts, remaining


def organize_json(source, language):
    original = source['extraction'].get('structured_resume')
    if not isinstance(original, dict):
        return None
    renderer.validate(original, draft=True)
    data = copy.deepcopy(original)
    data['facts_confirmed'] = False
    data['language'] = language
    visible = dict(renderer.iter_visible_fields(data))
    facts, remaining = [], []
    for item in segments(source):
        path = item['locator'].get('json_path') or item['locator'].get('path')
        candidates = [path] if path in visible else [p for p, value in visible.items() if value == item['text']]
        record = claim(item, interpretation='structured_source' if candidates else 'source_metadata')
        record['resume_paths'] = candidates
        facts.append(record)
        if not candidates:
            remaining.append({'claim_index': len(facts) - 1, 'reason': 'source_metadata_preserved'})
    return data, facts, remaining


def assemble(sources, language, role=None):
    profiles = []
    for source in sources:
        profiles.append(organize_json(source, language) or organize_text(source, language))
    data, records, remaining = new_profile(language), [], []
    if role:
        data['role'] = role
    elif len(profiles) == 1 and 'role' in profiles[0][0]:
        data['role'] = profiles[0][0]['role']
    if len(profiles) == 1 and 'example_notice' in profiles[0][0]:
        data['example_notice'] = profiles[0][0]['example_notice']
    names, headlines, conflicts = {}, {}, []
    for profile, facts, unclassified in profiles:
        if profile['name']:
            names.setdefault(profile['name'], []).extend(f for f in facts if 'name' in f['resume_paths'])
        if profile['headline']:
            headlines.setdefault(profile['headline'], []).extend(f for f in facts if 'headline' in f['resume_paths'])
    if len(names) == 1:
        data['name'] = next(iter(names))
    if len(headlines) == 1:
        data['headline'] = next(iter(headlines))
    if len(names) > 1:
        conflicts.append({'kind': 'name_candidates_disagree', 'message': 'Names differ across sources; no name selected.', 'values': list(names)})
    if len(headlines) > 1:
        conflicts.append({'kind': 'headline_candidates_disagree', 'message': 'Headlines differ across sources; none selected.', 'values': list(headlines)})
    entry_dates = {}
    for source_index, (profile, facts, unclassified) in enumerate(profiles):
        section_offset = len(data['sections'])
        contacts_map = {}
        for index, value in enumerate(profile['contacts']):
            if value not in data['contacts']:
                data['contacts'].append(value)
            contacts_map[f'contacts[{index}]'] = f'contacts[{data["contacts"].index(value)}]'
        for section in profile['sections']:
            item = copy.deepcopy(section)
            existing = {s['id'] for s in data['sections']}
            if item['id'] in existing:
                suffix = 2
                while f'{item["id"]}-{suffix}' in existing:
                    suffix += 1
                item['id'] += f'-{suffix}'
            data['sections'].append(item)
            for entry_index, entry in enumerate(item['entries']):
                if entry.get('heading') and entry.get('date'):
                    entry_dates.setdefault(' '.join(entry['heading'].casefold().split()), []).append({
                        'date': entry['date'],
                        'path': f'sections[{len(data["sections"])-1}].entries[{entry_index}].date'})
        record_offset = len(records)
        for fact in facts:
            mapped = []
            for path in fact['resume_paths']:
                if path.startswith('sections['):
                    mapped.append(re.sub(r'^sections\[(\d+)\]', lambda m: f'sections[{int(m[1])+section_offset}]', path))
                elif path in contacts_map:
                    mapped.append(contacts_map[path])
                elif path == 'name' and len(names) > 1 or path == 'headline' and len(headlines) > 1:
                    data['sections'].append({'id': f'candidate-{source_index+1}-{len(data["sections"])}',
                                             'title': '冲突候选原文' if language == 'zh' else 'Conflicting Source Candidates',
                                             'entries': [{'bullets': [fact['text']]}]})
                    mapped.append(f'sections[{len(data["sections"])-1}].entries[0].bullets[0]')
                else:
                    mapped.append(path)
            fact['resume_paths'] = mapped
            fact['id'] = f'claim-{len(records)+1:06d}'
            records.append(fact)
        for block in unclassified:
            record = records[record_offset + block['claim_index']]
            remaining.append({'fact_id': record['id'], 'text': record['text'], 'reason': block['reason'],
                              'source': record['source']})
    for conflict in conflicts:
        candidates = names if conflict['kind'] == 'name_candidates_disagree' else headlines
        ids = {fact['id'] for value in conflict['values'] for fact in candidates[value]}
        conflict['fact_ids'] = [fact['id'] for fact in records if fact['id'] in ids]
    for heading, entries in entry_dates.items():
        dates = {entry['date'] for entry in entries}
        if len(dates) > 1:
            paths = {entry['path'] for entry in entries}
            conflicts.append({'kind': 'entry_dates_disagree', 'message': 'Same candidate entry has different dates; entries retained separately.',
                              'heading': heading, 'values': sorted(dates),
                              'fact_ids': [fact['id'] for fact in records
                                           if paths.intersection(fact['resume_paths'])]})
    renderer.validate(data, draft=True)
    evidence = {'schema_version': 1, 'facts': records, 'remaining_blocks': remaining, 'conflicts': conflicts,
                'coverage': {'source_blocks': sum(len(s['extraction']['blocks']) for s in sources),
                             'segments': sum(len(list(segments(s))) for s in sources),
                             'accounted_segments': len(records), 'unclassified_segments': len(remaining)}}
    if evidence['coverage']['segments'] != len(records):
        raise ValueError('import coverage mismatch; source segments were not fully accounted for')
    return data, evidence


def read_sources(inputs, extractor=None):
    if not inputs or len(inputs) > MAX_SOURCES:
        raise ValueError(f'provide between 1 and {MAX_SOURCES} local input files')
    extract = extractor or local_module('resume_extract').extract
    sources, seen = [], set()
    for index, original_path in enumerate(inputs):
        requested = Path(original_path).expanduser().absolute()
        source = requested.resolve()
        if source in seen:
            raise ValueError(f'{requested}: duplicate source file')
        seen.add(source)
        if not source.is_file():
            raise ValueError(f'{requested}: input must be a local file')
        size = source.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise ValueError(f'{requested}: input exceeds {MAX_SOURCE_BYTES} bytes; nothing was truncated')
        before = renderer.file_hash(source)
        extracted = extract(source)
        if not isinstance(extracted, dict) or not isinstance(extracted.get('blocks'), list):
            raise ValueError(f'{requested}: extractor did not return a valid blocks list')
        extraction_status = str(extracted.get('status', 'ok')).casefold().replace('-', '_')
        if extraction_status in ('needs_dependency', 'unsupported', 'failed', 'error', 'empty'):
            diagnostic = '; '.join(str(w) for w in extracted.get('warnings', []))
            raise ValueError(f'{requested}: extraction {extraction_status}; {diagnostic or "no usable source content"}')
        if len(extracted['blocks']) > MAX_BLOCKS:
            raise ValueError(f'{requested}: extracted block count exceeds {MAX_BLOCKS}; nothing was truncated')
        ids, chars = set(), 0
        for block_index, block in enumerate(extracted['blocks']):
            label = f'{requested}: blocks[{block_index}]'
            if not isinstance(block, dict) or not isinstance(block.get('id'), str) or not block['id']:
                raise ValueError(f'{label}: block id must be a non-empty string')
            if block['id'] in ids:
                raise ValueError(f'{label}: duplicate block id')
            ids.add(block['id'])
            if not isinstance(block.get('text'), str) or not isinstance(block.get('locator'), dict):
                raise ValueError(f'{label}: text and locator are required')
            renderer.require_text(block['text'], label)
            chars += len(block['text'])
        if chars > MAX_TEXT_CHARS:
            raise ValueError(f'{requested}: extracted text exceeds {MAX_TEXT_CHARS} characters; nothing was truncated')
        if not any(block['text'].strip() for block in extracted['blocks']):
            raise ValueError(f'{requested}: extraction has no usable text; check the format or OCR dependencies')
        after = renderer.file_hash(source)
        metadata = extracted.get('source', {})
        if not isinstance(metadata, dict):
            raise ValueError(f'{requested}: extractor source metadata must be an object')
        extractor_hash = metadata.get('sha256')
        if before != after or extractor_hash and extractor_hash != before:
            raise ValueError(f'{requested}: source changed during extraction; retry with a stable copy')
        sources.append({'id': f'source-{index+1:03d}', 'requested_path': str(requested),
                        'resolved_path': str(source), 'sha256': before, 'bytes': size,
                        'via_symlink': requested != source, 'extraction': extracted})
    if sum(len(s['extraction']['blocks']) for s in sources) > MAX_BLOCKS:
        raise ValueError(f'total extracted blocks exceed {MAX_BLOCKS}; nothing was truncated')
    if sum(len(b['text']) for s in sources for b in s['extraction']['blocks']) > MAX_TEXT_CHARS:
        raise ValueError(f'total extracted text exceeds {MAX_TEXT_CHARS} characters; nothing was truncated')
    return sources


def evidence_markdown(evidence):
    lines = ['# Imported source evidence', '', 'All claims are source_only and require review; no facts were automatically confirmed.', '']
    for fact in evidence['facts']:
        source = fact['source']
        lines += [f'## {fact["id"]}', '', f'- Status: {fact["status"]}',
                  f'- Source: {source["id"]} / {source["block_id"]}',
                  '- Locator: ' + renderer.md_escape(json.dumps(source['locator'], ensure_ascii=False)),
                  '- Resume paths: ' + ', '.join(fact['resume_paths']),
                  '- Interpretation: ' + fact['interpretation'], '', renderer.md_escape(fact['text']), '']
        if source.get('links'):
            lines += ['Source links: ' + renderer.md_escape(json.dumps(source['links'], ensure_ascii=False)), '']
    return '\n'.join(lines)


def import_files(inputs, output, language=None, role=None, extractor=None, coaching=None, target=None):
    if language is not None and language not in ('zh', 'en'):
        raise ValueError('language must be zh or en')
    if role is not None and role not in renderer.ROLES:
        raise ValueError('role must be a known profession')
    if target is not None:
        renderer.require_text(target, 'target', allow_empty=False)
        if len(target) > 1000:
            raise ValueError('target must be at most 1000 characters')
    paths = [Path(path).expanduser().resolve() for path in inputs]
    out = safe_output(paths, output)
    sources = read_sources(inputs, extractor)
    combined = '\n'.join(block['text'] for source in sources for block in source['extraction']['blocks'])
    declared = {source['extraction']['structured_resume']['language'] for source in sources
                if isinstance(source['extraction'].get('structured_resume'), dict)}
    if language:
        chosen, language_method = language, 'user_override'
    elif len(declared) == 1:
        chosen, language_method = next(iter(declared)), 'structured_source_declaration'
    else:
        chosen, language_method = language_choice(combined), 'text_heuristic'
    data, evidence = assemble(sources, chosen, role)
    report = {'schema_version': 1, 'status': 'DRAFT_NEEDS_REVIEW', 'needs_review': True, 'language': chosen,
              'language_method': language_method,
              'role': role, 'requested_target': target, 'source_count': len(sources), 'facts_confirmed': False,
              'coverage': evidence['coverage'], 'conflicts': evidence['conflicts'],
              'source_statuses': [{'id': s['id'], 'status': s['extraction'].get('status', 'ok'),
                                   'format': s['extraction'].get('format'), 'method': s['extraction'].get('method'),
                                   'needs_review': s['extraction'].get('needs_review', True)} for s in sources],
              'warnings': [f'{source["id"]}: {w}' for source in sources for w in source['extraction'].get('warnings', [])],
              'structure_candidates': [{'fact_id': f['id'], 'kind': f['interpretation'], 'paths': f['resume_paths']}
                                       for f in evidence['facts'] if f['interpretation'].endswith('_candidate')],
              'notice': 'Imported content is source material, not verified fact. Review candidates, dates, reading order and unclassified blocks before final delivery.'}
    if any(source['via_symlink'] for source in sources):
        report['warnings'].append('Symlink or aliased input resolved to the reported real path; original input was not changed.')
    if any(layout_risk(source) or any('table' in block['locator'] for block in source['extraction']['blocks'])
           for source in sources):
        report['warnings'].append('Layout ambiguity: adjacent heading/date merging was disabled for affected sources or table blocks.')
    if not role:
        report['warnings'].append('Target role was not inferred; choose a role or JD during the conversation.')
    if evidence['remaining_blocks']:
        report['warnings'].append('Unclassified source text or metadata was preserved; review grouping and candidate headings.')
    if any(f['interpretation'] == 'continuation_unmerged_candidate' for f in evidence['facts']):
        report['warnings'].append('Possible continuation lines could not be safely joined across blocks or source regions; review their separate bullets.')
    if any(f['interpretation'] == 'continuation_hyphen_candidate' for f in evidence['facts']):
        report['warnings'].append('Printed line-end hyphens were retained while joining continuation lines; compare their spelling against the source.')
    if language:
        report['warnings'].append('Language override changes rendering metadata only; original text was not translated.')
    if len(declared) > 1:
        report['warnings'].append('Structured sources declare different languages; selected rendering language needs review.')
    for source in sources:
        if str(source['extraction'].get('status', '')).casefold() == 'partial':
            report['warnings'].append(f'{source["id"]}: partial extraction; original missing-page or region diagnostics remain in source.json.')
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.resume-import-', dir=out.parent))
    try:
        def write_json(name, value):
            (staging / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        write_json('source.json', {'schema_version': 1, 'sources': sources})
        source_lines = []
        for source in sources:
            source_lines += [f'=== {source["id"]}: {Path(source["resolved_path"]).name} ===']
            for block in source['extraction']['blocks']:
                source_lines += [f'[{block["id"]} | {json.dumps(block["locator"], ensure_ascii=False)}]', block['text'], '']
        (staging / 'source.txt').write_text('\n'.join(source_lines), encoding='utf-8')
        write_json('resume.json', data)
        (staging / 'resume.md').write_text(renderer.render_text(data, markdown=True, draft=True), encoding='utf-8')
        write_json('evidence.json', evidence)
        (staging / 'evidence.md').write_text(evidence_markdown(evidence), encoding='utf-8')
        write_json('import-report.json', report)
        if coaching is not None:
            review = coaching(data, evidence, report)
            if not isinstance(review, dict):
                raise ValueError('coaching callback must return an object')
            write_json('review.json', review)
            if isinstance(review.get('markdown'), str):
                (staging / 'review.md').write_text(review['markdown'], encoding='utf-8')
        else:
            coach = local_module('resume_coach')
            review = coach.plan(data, evidence, report, target=target)
            write_json('review.json', review)
            (staging / 'review.md').write_text(coach.render_review(review, language=chosen), encoding='utf-8')
        # An existing empty directory is replaceable atomically. A new user file
        # added during extraction makes this operation fail without overwriting it.
        safe_output(paths, output)
        os.replace(staging, out)
        staging = None
        return {'output': str(out), 'status': report['status'], 'sources': len(sources),
                'facts': len(evidence['facts']), 'remaining_blocks': len(evidence['remaining_blocks']),
                'conflicts': len(evidence['conflicts']), 'language': chosen}
    finally:
        if staging is not None:
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', nargs='+', type=Path)
    parser.add_argument('--out', required=True, type=Path, help='new or empty private output directory')
    parser.add_argument('--language', choices=('zh', 'en'), help='override language heuristic; does not translate source text')
    parser.add_argument('--role', choices=renderer.ROLES, help='optional target role; never inferred from history')
    parser.add_argument('--target', help='current application goal or direction, recorded as user-supplied context')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(import_files(args.inputs, args.out, args.language, args.role, target=args.target), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
