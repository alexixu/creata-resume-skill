#!/usr/bin/env python3
"""Create resume skeletons and render reviewed data. Python standard library only."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
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
PLACEHOLDER = re.compile(r'【[^】]*】|\[TODO[^\]]*\]|\b(?:TODO|TBD|YYYY)\b', re.I)


def skeleton(role, language):
    template = ROLES[role]
    return {'language': language, 'role': role, 'name': '', 'headline': '', 'contacts': [],
            'facts_confirmed': False,
            'sections': [{'id': key, 'title': TITLES[key][language == 'en'], 'entries': []}
                         for key in template['sections']]}


def require_text(value, label, allow_empty=True):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f'{label} must be a {"non-empty " if not allow_empty else ""}string')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f'{label} contains unsupported control characters')


def validate(data, draft=False):
    if not isinstance(data, dict):
        raise ValueError('resume must be a JSON object')
    if data.get('language') not in ('zh', 'en'):
        raise ValueError('language must be zh or en')
    for key in ('name', 'headline'):
        require_text(data.get(key), key, allow_empty=draft or key == 'headline')
    if type(data.get('facts_confirmed')) is not bool:
        raise ValueError('facts_confirmed must be true or false')
    contacts = data.get('contacts')
    if not isinstance(contacts, list) or (not draft and not contacts):
        raise ValueError('contacts must be a list with at least one contact for final output')
    for contact in contacts:
        require_text(contact, 'contact', allow_empty=False)
    sections = data.get('sections')
    if not isinstance(sections, list) or not sections:
        raise ValueError('sections must be a non-empty list')
    seen = set()
    visible_text = [data['name'], data['headline'], *contacts]
    bullet_count = 0
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError('each section must be an object')
        key = section.get('id')
        require_text(key, 'section id', allow_empty=False)
        if key in seen:
            raise ValueError(f'duplicate section id: {key}')
        seen.add(key)
        require_text(section.get('title'), 'section title', allow_empty=False)
        visible_text.append(section['title'])
        entries = section.get('entries')
        if not isinstance(entries, list):
            raise ValueError('entries must be a list')
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('each entry must be an object')
            for field in ('heading', 'date'):
                require_text(entry.get(field, ''), field)
                visible_text.append(entry.get(field, ''))
            bullets = entry.get('bullets', [])
            if not isinstance(bullets, list):
                raise ValueError('bullets must be a list of strings')
            for bullet in bullets:
                require_text(bullet, 'bullet', allow_empty=False)
            if not entry.get('heading', '').strip() and not bullets:
                raise ValueError('entry must have a heading or bullets')
            bullet_count += len(bullets)
            visible_text.extend(bullets)
    if not draft:
        if not data['facts_confirmed']:
            raise ValueError('facts not confirmed; resolve unknown claims or use --draft')
        if not bullet_count:
            raise ValueError('final output needs substantive content')
        if PLACEHOLDER.search('\n'.join(visible_text)):
            raise ValueError('unresolved placeholder; resolve it or use --draft')


def tex_escape(value):
    escapes = {'\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$',
               '#': r'\#', '_': r'\_', '{': r'\{', '}': r'\}',
               '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(escapes.get(char, char) for char in ' '.join(value.split()))


def md_escape(value):
    return re.sub(r'([\\`*_{}\[\]<>#!|])', r'\\\1', ' '.join(value.split()))


def render_text(data, markdown=False, draft=False):
    esc = md_escape if markdown else lambda text: ' '.join(text.split())
    lines = [('# ' if markdown else '') + esc(data['name'])]
    if draft:
        lines += ['DRAFT / 草稿 - 未完成事实与交付确认']
    if data['headline']:
        lines += [esc(data['headline'])]
    lines += [esc(c) for c in data['contacts']]
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
    lines = [r'\documentclass{resume}', r'\begin{document}', r'\pagenumbering{gobble}', r'\raggedright',
             r'\name{' + e(data['name']) + '}']
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
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).replace('\u00ad', ''))


def extraction_variants(extracted):
    # Preserve ordinary hyphens and minus signs; additionally accept line-end TeX hyphenation.
    joined = re.sub(r'(?<=\w)-[ \t]*\n[ \t]*(?=\w)', '', extracted)
    return (normalized_text(extracted), normalized_text(joined))


def build_pdf(out, max_pages):
    for binary in ('tectonic', 'pdfinfo', 'pdftotext'):
        if not shutil.which(binary):
            raise ValueError(f'{binary} is required for --pdf; editable files were generated')
    build_log = run(['tectonic', '-X', 'compile', 'resume.tex', '--outdir', '.'], out)
    (out / 'build.log').write_text(build_log)
    info = run(['pdfinfo', 'resume.pdf'], out)
    pages = int(re.search(r'^Pages:\s+(\d+)', info, re.M)[1])
    size_match = re.search(r'^Page size:\s+([\d.]+) x ([\d.]+)', info, re.M)
    is_a4 = bool(size_match and abs(float(size_match[1]) - 595.276) < 1
                 and abs(float(size_match[2]) - 841.89) < 1)
    run(['pdftotext', '-layout', 'resume.pdf', 'resume-extracted.txt'], out)
    extracted = (out / 'resume-extracted.txt').read_text()
    source = json.loads((out / 'resume.json').read_text())
    extracted_versions = extraction_variants(extracted)
    expected = [source['name'], source['headline'], *source['contacts']]
    for section in source['sections']:
        if section['entries']:
            expected.append(section['title'])
        for entry in section['entries']:
            expected.extend([entry.get('heading', ''), entry.get('date', ''), *entry.get('bullets', [])])
    missing = [index for index, value in enumerate(expected)
               if value and not any(normalized_text(value) in version for version in extracted_versions)]
    warnings = [line for line in build_log.splitlines()
                if any(word in line.lower() for word in ('overfull', 'missing character'))]
    report = {'pages': pages, 'a4': is_a4, 'within_page_limit': pages <= max_pages,
              'text_present': bool(extracted.strip()), 'missing_text_indices': missing,
              'layout_warnings': warnings, 'visual_review': 'NOT_RUN',
              'reading_order_review': 'NOT_RUN', 'ats_compatibility': 'NOT_CERTIFIED'}
    (out / 'qa.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if pages > max_pages or not is_a4 or not extracted.strip() or missing or warnings:
        raise ValueError('PDF QA requires correction; inspect qa.json and build.log')
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
            print(json.dumps(build_pdf(out, args.max_pages), ensure_ascii=False))
        print(out)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stdout:
            print(error.stdout, file=sys.stderr)
        sys.exit(1)
