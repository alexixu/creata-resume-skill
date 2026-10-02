#!/usr/bin/env python3
"""Create resume skeletons and render reviewed data. Python standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
ROLES = json.loads((ROOT / 'assets/role-templates.json').read_text())
TITLES = {
    'summary': ('个人概览', 'Summary'), 'experience': ('工作经历', 'Experience'),
    'projects': ('精选项目', 'Selected Projects'), 'education': ('教育背景', 'Education'),
    'skills': ('专业能力', 'Skills'), 'certifications': ('专业资格', 'Certifications'),
}
ICONS = {'summary': 'faUser', 'experience': 'faBriefcase', 'projects': 'faFileTextO',
         'education': 'faGraduationCap', 'skills': 'faCogs', 'certifications': 'faFileTextO'}
PLACEHOLDER = re.compile(
    r'【\s*(?:待确认|待补充|待填写|待填)[^】]*】|\[TODO[^\]]*\]|\b(?:TODO|TBD|YYYY)\b', re.I)
ROLE_PLACEHOLDERS = {marker for template in ROLES.values()
                     for marker in re.findall(r'【[^】]*】', template['bullet_pattern'])}
URL_TEXT = re.compile(r'(?:https?://|www\.|[a-z0-9][a-z0-9.-]*\.[a-z]{2,}/)\S+', re.I)


def skeleton(role, language):
    template = ROLES[role]
    return {'language': language, 'role': role, 'name': '', 'headline': '', 'contacts': [],
            'facts_confirmed': False,
            'sections': [{'id': key, 'title': TITLES[key][language == 'en'], 'entries': []}
                         for key in template['sections']]}


def require_text(value, label, allow_empty=True):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f'{label} must be a {"non-empty " if not allow_empty else ""}string')
    if any(ord(c) < 32 and c not in '\r\n\t' for c in value):
        raise ValueError(f'{label} contains unsupported control characters')


def reject_unknown_fields(data, allowed, path=''):
    for key in data:
        if key not in allowed:
            field = f'{path}.{key}' if path else key
            raise ValueError(f'{field}: unknown field')


def iter_visible_fields(data):
    """Yield stable source paths in the field order used by PDF presence checks."""
    for key in ('name', 'headline'):
        yield key, data[key]
    for index, contact in enumerate(data['contacts']):
        yield f'contacts[{index}]', contact
    for index, section in enumerate(data['sections']):
        if not section['entries']:
            continue
        path = f'sections[{index}]'
        yield f'{path}.title', section['title']
        for entry_index, entry in enumerate(section['entries']):
            entry_path = f'{path}.entries[{entry_index}]'
            for key in ('heading', 'date'):
                yield f'{entry_path}.{key}', entry.get(key, '')
            for bullet_index, bullet in enumerate(entry.get('bullets', [])):
                yield f'{entry_path}.bullets[{bullet_index}]', bullet


def validate(data, draft=False):
    if not isinstance(data, dict):
        raise ValueError('resume must be a JSON object')
    reject_unknown_fields(data, {'language', 'role', 'name', 'headline', 'contacts',
                                 'facts_confirmed', 'sections', 'example_notice'})
    if 'role' in data:
        require_text(data['role'], 'role', allow_empty=False)
        if data['role'] not in ROLES:
            raise ValueError('role must be a known profession; use the roles command to list choices')
    if 'example_notice' in data:
        require_text(data['example_notice'], 'example_notice')
    if data.get('language') not in ('zh', 'en'):
        raise ValueError('language must be zh or en')
    for key in ('name', 'headline'):
        require_text(data.get(key), key, allow_empty=draft or key == 'headline')
    if type(data.get('facts_confirmed')) is not bool:
        raise ValueError('facts_confirmed must be true or false')
    contacts = data.get('contacts')
    if not isinstance(contacts, list) or (not draft and not contacts):
        raise ValueError('contacts must be a list with at least one contact for final output')
    for index, contact in enumerate(contacts):
        require_text(contact, f'contacts[{index}]', allow_empty=False)
    sections = data.get('sections')
    if not isinstance(sections, list) or not sections:
        raise ValueError('sections must be a non-empty list')
    seen = set()
    bullet_count = 0
    for index, section in enumerate(sections):
        path = f'sections[{index}]'
        if not isinstance(section, dict):
            raise ValueError(f'{path} must be an object')
        reject_unknown_fields(section, {'id', 'title', 'entries'}, path)
        key = section.get('id')
        require_text(key, f'{path}.id', allow_empty=False)
        if key in seen:
            raise ValueError(f'{path}.id: duplicate section id: {key}')
        seen.add(key)
        require_text(section.get('title'), f'{path}.title', allow_empty=False)
        entries = section.get('entries')
        if not isinstance(entries, list):
            raise ValueError(f'{path}.entries must be a list')
        for entry_index, entry in enumerate(entries):
            entry_path = f'{path}.entries[{entry_index}]'
            if not isinstance(entry, dict):
                raise ValueError(f'{entry_path} must be an object')
            reject_unknown_fields(entry, {'heading', 'date', 'bullets'}, entry_path)
            for field in ('heading', 'date'):
                require_text(entry.get(field, ''), f'{entry_path}.{field}')
            bullets = entry.get('bullets', [])
            if not isinstance(bullets, list):
                raise ValueError(f'{entry_path}.bullets must be a list of strings')
            for bullet_index, bullet in enumerate(bullets):
                require_text(bullet, f'{entry_path}.bullets[{bullet_index}]', allow_empty=False)
            if not entry.get('heading', '').strip() and not bullets:
                raise ValueError(f'{entry_path} must have a heading or bullets')
            bullet_count += len(bullets)
    if not draft:
        if not data['facts_confirmed']:
            raise ValueError('facts not confirmed; resolve unknown claims or use --draft')
        if not bullet_count:
            raise ValueError('final output needs substantive content')
        for path, value in iter_visible_fields(data):
            if PLACEHOLDER.search(value) or any(marker in value for marker in ROLE_PLACEHOLDERS):
                raise ValueError(f'{path}: unresolved placeholder; resolve it or use --draft')


def tex_escape(value):
    escapes = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
               '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
               '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    def literal(text):
        return ''.join(escapes.get(char, char) for char in text)

    # Insert only invisible break opportunities into URL-like spans. Escaping
    # each character first keeps even TeX syntax inside a URL literal and safe.
    value = ' '.join(value.split())
    parts, start = [], 0
    for match in URL_TEXT.finditer(value):
        parts.append(literal(value[start:match.start()]))
        parts.append(r'\allowbreak{}'.join(literal(char) for char in match.group()))
        start = match.end()
    parts.append(literal(value[start:]))
    return ''.join(parts)


def md_escape(value):
    return re.sub(r'([\\`*_{}\[\]<>#!|&+().=~-])', r'\\\1', ' '.join(value.split()))


def render_text(data, markdown=False, draft=False):
    esc = md_escape if markdown else lambda text: ' '.join(text.split())
    lines = [('# ' if markdown else '') + esc(data['name'])] if data['name'].strip() else []
    header = ['DRAFT / 草稿 - 未完成事实与交付确认'] if draft else []
    if data['headline']:
        header += [esc(data['headline'])]
    header += [esc(c) for c in data['contacts']]
    if markdown:
        # CommonMark soft breaks collapse; retain one visible line per header field.
        header = [line + '  ' for line in header[:-1]] + header[-1:]
    lines += header
    for section in data['sections']:
        if not section['entries']:
            continue
        lines += ['', ('## ' if markdown else '') + esc(section['title'])]
        for entry in section['entries']:
            heading = esc(entry.get('heading', ''))
            date = esc(entry.get('date', ''))
            if heading or date:
                lines += ['', ' | '.join(filter(None, [heading, date]))]
            lines += ['- ' + esc(b) for b in entry.get('bullets', [])]
    return '\n'.join(lines) + '\n'


def render_tex(data, theme='classic', draft=False):
    e = tex_escape
    lines = [r'\documentclass[literaltext]{resume}', r'\begin{document}', r'\pagenumbering{gobble}', r'\raggedright']
    if data['name'].strip():
        lines += [r'\name{' + e(data['name']) + '}']
    if draft:
        lines += [r'\headline{DRAFT / 草稿 - 未完成事实与交付确认}']
    # Paragraphs wrap long contacts and headlines rather than clipping centerline boxes.
    lines += [r'{\centering\small']
    if theme == 'plain':
        lines += [e(c) + r'\par' for c in data['contacts']]
    else:
        lines += [r' \textperiodcentered\ '.join(e(c) for c in data['contacts']) + r'\par']
    lines += [r'}\vspace{0.7ex}']
    if data['headline']:
        lines += [r'{\centering ' + e(data['headline']) + r'\par}\vspace{0.6ex}']
    for section in data['sections']:
        if not section['entries']:
            continue
        icon = '\\' + ICONS.get(section['id'], 'faFileTextO') + r'\ ' if theme == 'classic' else ''
        lines += [r'\section{' + icon + e(section['title']) + '}']
        for entry in section['entries']:
            heading, date = entry.get('heading', ''), entry.get('date', '')
            if theme == 'plain':
                if heading:
                    lines += [r'\textbf{' + e(heading) + r'}\par']
                if date:
                    lines += [e(date) + r'\par']
            elif heading or date:
                lines += [r'\datedsubsection{' + e(heading) + '}{' + e(date) + '}']
            if entry.get('bullets'):
                lines += [r'\begin{itemize}'] + [r'\item ' + e(b) for b in entry['bullets']] + [r'\end{itemize}']
    return '\n'.join(lines + [r'\end{document}', ''])


def run(command, cwd):
    return subprocess.run(command, cwd=cwd, env={**os.environ, 'SOURCE_DATE_EPOCH': '946684800'},
                          check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout


def normalized_text(value):
    return unicodedata.normalize('NFKC', value).replace('\u00ad', '')


def text_pattern(value):
    """Allow observed types of PDF wrapping without joining separate Latin words."""
    expected = ' '.join(normalized_text(value).split())
    if not expected:
        return re.compile('')
    latin = lambda char: unicodedata.name(char, '').startswith('LATIN ')
    cjk = lambda char: any(unicodedata.name(char, '').startswith(prefix)
                           for prefix in ('CJK ', 'HIRAGANA ', 'KATAKANA ', 'HANGUL '))
    urls = [match.span() for match in URL_TEXT.finditer(expected)]
    parts = []
    # Latin word boundaries stop SQL from matching NoSQL and dates from matching
    # longer numbers. CJK has no whitespace-delimited word boundary.
    word = r'A-Za-z0-9_\u00c0-\u024f\u1e00-\u1eff'
    if latin(expected[0]) or expected[0].isdigit():
        parts.append(r'(?<![' + word + '])')
    for index, char in enumerate(expected):
        if index:
            before = expected[index - 1]
            if before != ' ' and char != ' ':
                if any(start < index < end for start, end in urls):
                    parts.append(r'(?:[ \t]*\n[ \t]*)?')
                elif latin(before) and latin(char):
                    parts.append(r'(?:-[ \t]*\n[ \t]*)?')
                elif before == '-' and index > 1 and latin(expected[index - 2]) and latin(char):
                    parts.append(r'(?:[ \t]*\n[ \t]*)?')
                elif cjk(before) or cjk(char):
                    parts.append(r'\s*')
        if char == ' ':
            adjacent_cjk = ((index and cjk(expected[index - 1]))
                            or (index + 1 < len(expected) and cjk(expected[index + 1])))
            left = expected[:index].rsplit(' ', 1)[-1]
            right = expected[index + 1:].split(' ', 1)[0]
            # Poppler can join narrow gaps between initials (B C -> BC).
            # This exception never joins ordinary multi-letter words.
            initials = all(len(token) == 1 and token.isupper() and latin(token)
                           for token in (left, right))
            parts.append(r'\s*' if adjacent_cjk or initials else r'\s+')
        else:
            parts.append(re.escape(char))
    if latin(expected[-1]) or expected[-1].isdigit():
        parts.append(r'(?![' + word + '])')
    return re.compile(''.join(parts))


def text_is_present(value, extracted):
    return text_pattern(value).search(normalized_text(extracted)) is not None


def missing_text_indices(expected, extracted):
    """Reserve distinct spans, preferring complete long fields over their substrings."""
    text = normalized_text(extracted)
    occupied, missing = [], []
    # Priority measures content, not optional spacing (for example spaced CJK
    # characters or initials). The matcher still requires ordinary Latin spaces.
    for index in sorted(range(len(expected)),
                        key=lambda i: len(''.join(normalized_text(expected[i][1]).split())), reverse=True):
        value = expected[index][1]
        if not value.strip():
            continue
        # Prefer the complete same-line occurrence over a coincidental match
        # spanning the end/start of two separate fields. Wrapping remains valid
        # when no shorter occurrence is available.
        matches = sorted(text_pattern(value).finditer(text),
                         key=lambda match: (match.group().count('\n') + match.group().count('\f'),
                                            match.end() - match.start(), match.start()))
        for match in matches:
            start, end = match.span()
            if not any(start < right and end > left for left, right in occupied):
                occupied.append((start, end))
                break
        else:
            missing.append(index)
    return sorted(missing)


GENERATED_FILES = ('qa.json', 'build.log', 'build-state.json', 'build-dependencies.mk',
                   'resume.pdf', 'resume-extracted.txt', 'resume.aux', 'resume.log',
                   'resume.xdv', 'resume.synctex.gz', 'resume.out', 'resume.toc')


def safe_target(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise ValueError(f'unsafe generated target {path.name}: symlinks and non-regular files are refused')


def safe_write(path, text):
    """Atomically replace a regular target; never open an existing link for writing."""
    safe_target(path)
    fd, name = tempfile.mkstemp(prefix='.resume-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        safe_target(path)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def install_generated(source, destination):
    safe_target(destination)
    os.replace(source, destination)


def file_hash(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def dependency_names(out):
    """Read Tectonic's one-dependency-per-continuation-line Makefile output.

    Paths can contain spaces; shell-style word splitting would corrupt them.
    Keep symlink paths rather than resolving their targets so retargeting a
    private dependency also invalidates the recorded build.
    """
    rules = (out / 'build-dependencies.mk').read_text()
    _, separator, body = rules.partition(' : ')
    if not separator:
        raise ValueError('cannot read Tectonic build-dependencies.mk; run rebuild again')
    names = {'resume.json', 'resume.tex', 'resume.pdf', 'build.log', 'build-dependencies.mk'}
    for line in body.splitlines():
        value = line.strip().removesuffix(' \\')
        if not value:
            continue
        path = Path(os.path.abspath(out / value))
        try:
            name = str(path.relative_to(out))
        except ValueError:
            name = str(path)
        names.add(name)
    return sorted(names)


def snapshot_build(out):
    hashes = {name: file_hash(out / name) for name in dependency_names(out)}
    missing = [name for name, digest in hashes.items() if digest is None]
    if missing:
        raise ValueError('build dependency missing: ' + ', '.join(missing))
    state = {'version': 1, 'hash_algorithm': 'sha256', 'input_hashes': hashes}
    safe_write(out / 'build-state.json', json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    return hashes


def verify_build_state(out, report):
    try:
        state = json.loads((out / 'build-state.json').read_text())
    except (OSError, ValueError) as error:
        raise ValueError('no readable build-state.json; run rebuild before check') from error
    hashes = state.get('input_hashes') if isinstance(state, dict) else None
    required = {'resume.json', 'resume.tex', 'resume.pdf', 'build.log', 'build-dependencies.mk'}
    if (not isinstance(hashes, dict) or not required.issubset(hashes)
            or state.get('version') != 1 or state.get('hash_algorithm') != 'sha256'
            or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                   for value in hashes.values())):
        raise ValueError('invalid build-state.json; run rebuild before check')
    current = {name: file_hash(out / name) for name in hashes}
    report['input_hashes'] = current
    changed = [name for name in hashes if current[name] != hashes[name]]
    if changed:
        report['stale_files'] = changed
        raise ValueError('build is stale or files are missing: ' + ', '.join(changed)
                         + '; run rebuild before check')
    return current


def build_pdf(out, max_pages, *, compile_pdf=True, draft=None):
    out = out.resolve()
    # This preflight precedes even error-report writes. A hostile report link must
    # produce a CLI diagnostic, not overwrite the file that the link points to.
    for name in GENERATED_FILES:
        safe_target(out / name)
    report = {'max_pages': max_pages, 'visual_review': 'NOT_RUN', 'reading_order_review': 'NOT_RUN',
              'ats_compatibility': 'NOT_CERTIFIED', 'hash_algorithm': 'sha256',
              'operation': 'rebuild' if compile_pdf else 'check'}
    completed = []
    build_log = ''
    stage = 'dependencies'
    try:
        if draft is not None:
            stage = 'source_validation'
            validate(json.loads((out / 'resume.json').read_text()), draft=draft)
            completed.append(stage)
        stage = 'dependencies'
        binaries = ('tectonic', 'pdfinfo', 'pdftotext') if compile_pdf else ('pdfinfo', 'pdftotext')
        for binary in binaries:
            if not shutil.which(binary):
                raise ValueError(f'{binary} is required for --pdf; editable files were generated')
        completed.append(stage)
        if compile_pdf:
            stage = 'compile'
            # Read the user's existing sources in place, but let the compiler write
            # only into a fresh private directory. Do not copy/replace private TeX.
            with tempfile.TemporaryDirectory(prefix='.resume-build-', dir=out) as temporary:
                generated = Path(temporary)
                build_log = run(['tectonic', '-X', 'compile', 'resume.tex', '--outdir', str(generated),
                                 '--makefile-rules', str(generated / 'build-dependencies.mk')], out)
                # Tectonic prefixes relative *input* dependencies with --outdir,
                # although they were read relative to cwd. Rebase that prefix;
                # absolute external dependencies and symlink names stay intact.
                rules = generated / 'build-dependencies.mk'
                safe_write(rules, rules.read_text().replace(str(generated) + '/', str(out) + '/'))
                for name in ('resume.pdf', 'build-dependencies.mk'):
                    install_generated(generated / name, out / name)
            safe_write(out / 'build.log', build_log)
            completed.append(stage)
        else:
            stage = 'freshness'
            verify_build_state(out, report)
            build_log = (out / 'build.log').read_text()
            completed.append(stage)
        stage = 'pdfinfo'
        info = run(['pdfinfo', 'resume.pdf'], out)
        page_match = re.search(r'^Pages:\s+(\d+)', info, re.M)
        if not page_match or int(page_match[1]) < 1:
            raise ValueError('pdfinfo did not report a page count')
        pages = int(page_match[1])
        report.update(pages=pages, within_page_limit=pages <= max_pages)
        sizes = run(['pdfinfo', '-f', '1', '-l', str(pages), 'resume.pdf'], out)
        matches = re.findall(r'^Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)', sizes, re.M)
        if [int(page) for page, _, _ in matches] != list(range(1, pages + 1)):
            raise ValueError('pdfinfo did not report exactly one size for every PDF page')
        page_sizes = [{'page': int(page), 'width_pt': float(width), 'height_pt': float(height),
                       'a4': abs(float(width) - 595.276) < 1 and abs(float(height) - 841.89) < 1}
                      for page, width, height in matches]
        bad_pages = [item['page'] for item in page_sizes if not item['a4']]
        is_a4 = not bad_pages
        report.update(a4=is_a4, page_sizes=page_sizes, non_a4_pages=bad_pages)
        completed.append(stage)
        stage = 'text_extraction'
        extracted = run(['pdftotext', '-layout', 'resume.pdf', '-'], out)
        safe_write(out / 'resume-extracted.txt', extracted)
        report['text_present'] = bool(extracted.strip())
        completed.append(stage)
        stage = 'source_read'
        source = json.loads((out / 'resume.json').read_text())
        completed.append(stage)
        stage = 'provenance' if compile_pdf else 'freshness'
        if compile_pdf:
            report['input_hashes'] = snapshot_build(out)
            completed.append(stage)
        else:
            # Recheck after reading the PDF in case an input changed during QA.
            verify_build_state(out, report)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        output = (error.stdout or '') if isinstance(error, subprocess.CalledProcessError) else ''
        diagnostic = f'\n[{stage} failed]\n{error}\n{output}'
        if compile_pdf:
            safe_write(out / 'build.log', build_log + diagnostic)
        report.update(status='FAILED', stage=stage, error=str(error),
                      failure_reasons=[f'{stage} failed: {error}'], completed_stages=completed)
        safe_write(out / 'qa.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        message = f'PDF {stage} failed: {error}; inspect qa.json'
        if compile_pdf:
            message += ' and build.log'
        raise ValueError(message + (f'\n{output}' if output else '')) from error
    expected = list(iter_visible_fields(source))
    missing = missing_text_indices(expected, extracted)
    missing_fields = [expected[index][0] for index in missing]
    warnings = list(dict.fromkeys(line for line in build_log.splitlines()
                                 if any(word in line.lower() for word in ('overfull', 'missing character'))))
    reasons = []
    if pages > max_pages:
        reasons.append(f'page limit exceeded: {pages} pages (maximum {max_pages})')
    if not is_a4:
        for size in page_sizes:
            if not size['a4']:
                reasons.append(f'page {size["page"]} size is not A4: '
                               f'{size["width_pt"]:g} x {size["height_pt"]:g} pt')
    if not extracted.strip():
        reasons.append('no extractable text')
    if missing_fields:
        reasons.append('missing text at ' + ', '.join(missing_fields))
    if warnings:
        reasons.append(f'{len(warnings)} layout or missing-character warning(s); inspect build.log')
    if draft:
        report['draft_marked'] = text_is_present('DRAFT / 草稿', extracted)
        if not report['draft_marked']:
            reasons.append('draft marker missing: existing source must visibly include DRAFT / 草稿')
    completed.append('automated_checks')
    report.update(status='FAILED' if reasons else 'PASSED', stage='automated_checks',
                  completed_stages=completed, failure_reasons=reasons,
                  missing_text_indices=missing, missing_text_fields=missing_fields,
                  layout_warnings=warnings)
    if reasons:
        report['error'] = '; '.join(reasons)
    safe_write(out / 'qa.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if reasons:
        raise ValueError(f'PDF QA failed: {report["error"]}; inspect qa.json and build.log')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('roles', help='list profession templates')
    init = sub.add_parser('init', help='create an empty, unconfirmed resume skeleton')
    init.add_argument('--role', choices=ROLES, required=True)
    init.add_argument('--language', choices=('zh', 'en'), default='zh')
    init.add_argument('--output', type=Path, required=True)
    render = sub.add_parser('render', help='generate editable files; optionally build PDF')
    render.add_argument('input', type=Path)
    render.add_argument('--out', type=Path, required=True, help='new or empty output directory')
    render.add_argument('--theme', choices=('classic', 'plain'), default='classic')
    render.add_argument('--draft', action='store_true')
    render.add_argument('--pdf', action='store_true')
    render.add_argument('--max-pages', type=int, default=1, help='change only for user-requested page counts')
    for command, help_text in (
        ('rebuild', 'compile existing editable files and refresh PDF QA'),
        ('check', 'check an unchanged existing PDF without compiling'),
    ):
        existing = sub.add_parser(command, help=help_text)
        existing.add_argument('directory', type=Path)
        existing.add_argument('--draft', action='store_true', help='allow unconfirmed draft source data')
        existing.add_argument('--max-pages', type=int, default=1,
                              help='change only for user-requested page counts')
    args = parser.parse_args()
    if args.command == 'roles':
        for key, value in ROLES.items():
            print(f'{key:15} {value["label_zh"]} / {value["label_en"]}')
    elif args.command == 'init':
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as handle:
            json.dump(skeleton(args.role, args.language), handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        print(args.output.resolve())
    elif args.command in ('rebuild', 'check'):
        if args.max_pages < 1:
            raise ValueError('--max-pages must be positive')
        out = args.directory.resolve()
        if not out.is_dir():
            raise ValueError('directory must be an existing resume output directory')
        print(json.dumps(build_pdf(out, args.max_pages, compile_pdf=args.command == 'rebuild',
                                   draft=args.draft), ensure_ascii=False))
        print(out)
    else:
        if args.max_pages < 1:
            raise ValueError('--max-pages must be positive')
        data = json.loads(args.input.read_text())
        validate(data, args.draft)
        out = args.out.resolve()
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError('output must be new or empty; use a new version directory')
        out.mkdir(parents=True, exist_ok=True)
        for filename in ('resume.cls', 'fontawesome.sty', 'fontawesomesymbols-generic.tex',
                         'fontawesomesymbols-xeluatex.tex'):
            shutil.copy2(ROOT / filename, out / filename)
        shutil.copytree(ROOT / 'fonts', out / 'fonts')
        (out / 'resume.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        (out / 'resume.md').write_text(render_text(data, True, args.draft))
        (out / 'resume.txt').write_text(render_text(data, False, args.draft))
        (out / 'resume.tex').write_text(render_tex(data, args.theme, args.draft))
        if args.pdf:
            print(json.dumps(build_pdf(out, args.max_pages, draft=args.draft), ensure_ascii=False))
        print(out)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stdout:
            print(error.stdout, file=sys.stderr)
        sys.exit(1)
