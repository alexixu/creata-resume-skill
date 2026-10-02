"""Conservative resume structure, complete provenance and private draft imports."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_import', ROOT / 'scripts/import_resume.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)

ENGLISH = '''Sample Candidate
sample@example.com | https://example.com/portfolio
\uf0b1 Work Experience
Example Workflow Co / Software Engineer
Jul 2023 - Aug 2026
- Implemented validation for internal forms.
Projects
Store Reporting Template | Mar 2025 - Jun 2025
- Documented reporting fields for three stores.
Education
Example University / BS Information Systems
Sep 2017 - Jun 2021
Skills
- Python, SQL and regression testing
Other Original Notes
No quantified time saving was measured.
'''
CHINESE = '''张样例
sample@example.com
工作经历
示例公司 / 开发工程师
2023年7月-2026年8月
• 为内部表单实现输入校验。
项目经历
门店报表模板 | 2025.03–2025.06
• 整理三个门店的报表字段。
教育背景
示例大学 / 信息系统学士
2017.09-2021.06
专业技能
Python、SQL 与回归测试
'''


def extraction(text, warnings=None):
    return {'format': 'txt', 'method': 'test-lines', 'blocks': [
        {'id': f'line-{i+1}', 'text': line, 'locator': {'line': i+1}} for i, line in enumerate(text.splitlines())],
        'warnings': warnings or [], 'needs_review': True}


def source(text, source_id='source-001', warnings=None):
    return {'id': source_id, 'sha256': 'a' * 64, 'extraction': extraction(text, warnings)}


class ImportResumeTests(unittest.TestCase):
    def test_english_single_column_is_organized_not_merely_copied(self):
        data, evidence = importer.assemble([source(ENGLISH)], 'en', 'software')
        self.assertEqual(data['name'], 'Sample Candidate')
        self.assertEqual(len(data['contacts']), 1)
        sections = {s['id']: s for s in data['sections']}
        self.assertTrue({'experience', 'projects', 'education', 'skills'}.issubset(sections))
        experience = sections['experience']['entries'][0]
        self.assertEqual(experience['heading'], 'Example Workflow Co / Software Engineer')
        self.assertEqual(experience['date'], 'Jul 2023 - Aug 2026')
        self.assertEqual(experience['bullets'], ['Implemented validation for internal forms.'])
        self.assertEqual(sections['projects']['entries'][0]['date'], 'Mar 2025 - Jun 2025')
        self.assertFalse(data['facts_confirmed'])
        self.assertEqual(evidence['coverage']['segments'], evidence['coverage']['accounted_segments'])
        self.assertTrue(all(f['status'] == 'source_only' and f['needs_review'] for f in evidence['facts']))
        self.assertIn('\uf0b1 Work Experience', [f['text'] for f in evidence['facts']])
        self.assertNotIn('\uf0b1', sections['experience']['title'])

    def test_chinese_dates_names_and_section_candidates_are_traceable(self):
        data, evidence = importer.assemble([source(CHINESE)], 'zh')
        self.assertEqual(data['name'], '张样例')
        sections = {s['id']: s for s in data['sections']}
        self.assertEqual(sections['experience']['entries'][0]['date'], '2023年7月-2026年8月')
        self.assertEqual(sections['education']['entries'][0]['heading'], '示例大学 / 信息系统学士')
        date = next(f for f in evidence['facts'] if f['text'] == '2023年7月-2026年8月')
        self.assertEqual(date['interpretation'], 'date_candidate')
        self.assertEqual(date['source']['locator']['line'], 5)
        self.assertEqual(date['resume_paths'], ['sections[0].entries[0].date'])

    def test_indented_continuations_merge_within_block_and_keep_two_source_claims(self):
        text = 'Sample Candidate\nPortfolio: example.com/portfolio\nExperience\nExample Co / Engineer\n2023.07-2026.08\n  - Implemented explicit transition\n    validation and regression tests.\n  - Retained another bullet.'
        value = source(text)
        value['extraction']['blocks'] = [{'id': 'paragraph-1', 'text': text, 'locator': {'page': 1}}]
        data, evidence = importer.assemble([value], 'en')
        self.assertEqual(data['contacts'], ['Portfolio: example.com/portfolio'])
        entry = data['sections'][0]['entries'][0]
        self.assertEqual(entry['bullets'], ['Implemented explicit transition validation and regression tests.',
                                            'Retained another bullet.'])
        continuation = next(f for f in evidence['facts'] if f['interpretation'] == 'continuation_candidate')
        self.assertEqual(continuation['text'], '    validation and regression tests.')
        self.assertEqual(continuation['resume_paths'], ['sections[0].entries[0].bullets[0]'])
        self.assertEqual(evidence['coverage']['accounted_segments'], len(text.splitlines()))

    def test_continuations_do_not_merge_across_blocks_pages_or_new_bullets(self):
        text = 'Sample Candidate\nExperience\n- Implemented explicit transition\n  validation and regression tests.'
        value = source(text)
        data, evidence = importer.assemble([value], 'en')
        self.assertEqual(data['sections'][0]['entries'][0]['bullets'],
                         ['Implemented explicit transition', 'validation and regression tests.'])
        self.assertFalse(any(f['interpretation'] == 'continuation_candidate' for f in evidence['facts']))
        self.assertTrue(any(f['interpretation'] == 'continuation_unmerged_candidate' for f in evidence['facts']))

    def test_unmerged_and_hyphen_continuations_are_explicit_review_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / 'old.txt'
            text = 'Sample Candidate\nExperience\n- Built cross-\n  functional implementation.'
            path.write_text(text)
            for joined in (False, True):
                extracted = extraction(text)
                if joined:
                    extracted['blocks'] = [{'id': 'p1', 'text': text, 'locator': {'page': 1}}]
                out = base / ('joined' if joined else 'separate')
                importer.import_files([path], out, extractor=lambda _: extracted)
                report = json.loads((out / 'import-report.json').read_text())
                self.assertTrue(any(('hyphens' if joined else 'could not be safely joined') in warning
                                    for warning in report['warnings']))
                evidence = json.loads((out / 'evidence.json').read_text())
                self.assertTrue(any('cross-' in f['text'] for f in evidence['facts']))

    def test_multicolumn_risk_disables_adjacency_guessing_and_preserves_every_line(self):
        data, evidence = importer.assemble([source(ENGLISH, warnings=['Multiple columns; reading order requires review.'])], 'en')
        raw = next(s for s in data['sections'] if s['id'] == 'unclassified')
        self.assertIn('Jul 2023 - Aug 2026', raw['entries'][0]['bullets'])
        self.assertFalse(any(e.get('date') for s in data['sections'] for e in s['entries']))
        self.assertTrue(all(r['reason'] in ('layout_ambiguity', 'unsupported_section_heading')
                            for r in evidence['remaining_blocks']))
        self.assertEqual(evidence['coverage']['segments'], len(evidence['facts']))

    def test_general_reading_order_notice_does_not_disable_single_column_structure(self):
        data, evidence = importer.assemble([source(ENGLISH, warnings=[
            'PDF layout extraction does not establish semantic reading order; review source.'])], 'en')
        section = next(s for s in data['sections'] if s['id'] == 'experience')
        self.assertEqual(section['entries'][0]['date'], 'Jul 2023 - Aug 2026')
        self.assertEqual(section['entries'][0]['heading'], 'Example Workflow Co / Software Engineer')

    def test_table_blocks_do_not_disable_ordinary_body_paragraph_structure(self):
        value = source(ENGLISH, warnings=['Table cell reading order needs source review.'])
        value['extraction']['blocks'].append({'id': 'table-cell', 'text': 'Do not join this table company with a body date',
                                             'locator': {'table': 1, 'row': 0, 'column': 0}})
        data, evidence = importer.assemble([value], 'en')
        experience = next(s for s in data['sections'] if s['id'] == 'experience')
        self.assertEqual(experience['entries'][0]['date'], 'Jul 2023 - Aug 2026')
        table_record = next(f for f in evidence['facts'] if f['source']['block_id'] == 'table-cell')
        self.assertEqual(table_record['interpretation'], 'unclassified')
        self.assertTrue(any(r['fact_id'] == table_record['id'] and r['reason'] == 'layout_ambiguity'
                            for r in evidence['remaining_blocks']))

    def test_unknown_name_and_unclassified_content_remain_a_valid_draft(self):
        data, evidence = importer.assemble([source('Software Engineer\nNotes without a known section\nDo not delete this line')], 'en')
        self.assertEqual(data['name'], '')
        self.assertEqual(len(evidence['remaining_blocks']), 3)
        rendered = importer.renderer.render_text(data, draft=True)
        self.assertIn('Do not delete this line', rendered)
        importer.renderer.validate(data, draft=True)

    def test_explicit_name_after_intro_is_supported_and_dates_not_merged_across_sections(self):
        text = 'Resume\nName: Sample Candidate\nProjects\nFirst project\nSkills\n2025.03-2025.06\nPython'
        data, evidence = importer.assemble([source(text)], 'en')
        self.assertEqual(data['name'], 'Sample Candidate')
        self.assertFalse(any(e.get('date') for s in data['sections'] for e in s['entries']))
        self.assertEqual(len(evidence['facts']), len(text.splitlines()))

    def test_two_header_lines_and_date_first_headers_are_candidate_entries(self):
        text = 'Sample Candidate\nExperience\nExample Company\nSoftware Engineer\n2023.07-2026.08\n- Built validation.\n2021.01-2023.06\nExample Tools / Junior Engineer\n- Maintained forms.'
        data, evidence = importer.assemble([source(text)], 'en')
        entries = data['sections'][0]['entries']
        self.assertEqual(entries[0]['heading'], 'Example Company / Software Engineer')
        self.assertEqual(entries[1]['heading'], 'Example Tools / Junior Engineer')
        self.assertEqual(len(evidence['facts']), len(text.splitlines()))

    def test_headings_and_dates_are_not_joined_across_page_or_docx_region(self):
        value = source('Sample Candidate\nExperience\nExample Company\n2023.07-2026.08\n- Built validation.')
        value['extraction']['blocks'][2]['locator']['region'] = 'header'
        value['extraction']['blocks'][3]['locator']['region'] = 'body'
        data, evidence = importer.assemble([value], 'en')
        self.assertFalse(any(e.get('date') for s in data['sections'] for e in s['entries']))
        self.assertIn('2023.07-2026.08', importer.renderer.render_text(data, draft=True))
        self.assertEqual(len(evidence['facts']), 5)

    def test_multiple_sources_keep_disagreeing_name_and_dates_without_choosing(self):
        other = ENGLISH.replace('Sample Candidate', 'Another Candidate').replace('Jul 2023 - Aug 2026', 'Jul 2022 - Aug 2026')
        data, evidence = importer.assemble([source(ENGLISH), source(other, 'source-002')], 'en')
        self.assertEqual(data['name'], '')
        kinds = {c['kind'] for c in evidence['conflicts']}
        self.assertIn('name_candidates_disagree', kinds)
        self.assertIn('entry_dates_disagree', kinds)
        rendered = importer.renderer.render_text(data, draft=True)
        self.assertIn('Sample Candidate', rendered)
        self.assertIn('Another Candidate', rendered)
        self.assertIn('Jul 2023 - Aug 2026', rendered)
        self.assertIn('Jul 2022 - Aug 2026', rendered)
        self.assertEqual(len({s['id'] for s in data['sections']}), len(data['sections']))
        for f in evidence['facts']:
            self.assertTrue(f['resume_paths'])

    def test_structured_json_fact_approval_is_preserved_only_in_source_and_demoted(self):
        original = json.loads((ROOT / 'assets/examples/en.json').read_text())
        value = {'id': 'source-001', 'sha256': 'a'*64, 'extraction': {'structured_resume': original,
            'blocks': [{'id': f'field-{i}', 'text': text, 'locator': {'json_path': path}}
                       for i, (path, text) in enumerate(importer.renderer.iter_visible_fields(original))], 'warnings': []}}
        before = copy.deepcopy(value)
        data, evidence = importer.assemble([value], 'en')
        self.assertFalse(data['facts_confirmed'])
        self.assertEqual(data['sections'], original['sections'])
        self.assertEqual(data['role'], original['role'])
        self.assertTrue(value['extraction']['structured_resume']['facts_confirmed'])
        self.assertEqual(value, before)
        self.assertTrue(all(f['status'] == 'source_only' for f in evidence['facts']))

    def test_import_writes_editable_source_evidence_and_report_without_font_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_path, out = base / 'old.txt', base / 'imported'
            source_path.write_text(ENGLISH)
            original = source_path.read_bytes()
            result = importer.import_files([source_path], out, role='software',
                                           extractor=lambda _: extraction(ENGLISH), target='Backend workflow engineer')
            self.assertEqual(result['status'], 'DRAFT_NEEDS_REVIEW')
            self.assertEqual(source_path.read_bytes(), original)
            self.assertTrue({'source.json', 'source.txt', 'resume.json', 'resume.md', 'evidence.json',
                             'evidence.md', 'import-report.json', 'review.json', 'review.md'}.issubset(p.name for p in out.iterdir()))
            self.assertFalse((out / 'fonts').exists())
            self.assertIn('DRAFT', (out / 'resume.md').read_text())
            report = json.loads((out / 'import-report.json').read_text())
            self.assertEqual(report['requested_target'], 'Backend workflow engineer')
            self.assertIn('sha256', json.loads((out / 'source.json').read_text())['sources'][0])

    def test_previous_draft_input_path_repository_and_symlink_outputs_are_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / 'old.txt'
            path.write_text(ENGLISH)
            out = base / 'output'
            out.mkdir()
            sentinel = out / 'manual.txt'
            sentinel.write_text('keep')
            link = base / 'link'
            link.symlink_to(ROOT, target_is_directory=True)
            for destination in (out, path, base, ROOT / 'private-import', link / 'private-import'):
                with self.subTest(destination=destination), self.assertRaises(ValueError):
                    importer.import_files([path], destination, extractor=lambda _: extraction(ENGLISH))
            self.assertEqual(sentinel.read_text(), 'keep')
            self.assertEqual(path.read_text(), ENGLISH)

    def test_extraction_change_limits_and_invalid_blocks_fail_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path, out = base / 'old.txt', base / 'output'
            path.write_text(ENGLISH)

            def changed(source):
                source.write_text('changed')
                return extraction(ENGLISH)

            with self.assertRaisesRegex(ValueError, 'changed during extraction'):
                importer.import_files([path], out, extractor=changed)
            self.assertFalse(out.exists())
            path.write_text(ENGLISH)
            with mock.patch.object(importer, 'MAX_SOURCE_BYTES', 2):
                with self.assertRaisesRegex(ValueError, 'exceeds 2 bytes'):
                    importer.import_files([path], out, extractor=lambda _: extraction(ENGLISH))
            invalid = extraction(ENGLISH)
            invalid['blocks'][1]['id'] = invalid['blocks'][0]['id']
            with self.assertRaisesRegex(ValueError, 'duplicate block'):
                importer.import_files([path], out, extractor=lambda _: invalid)
            self.assertFalse(out.exists())

    def test_unsupported_missing_dependency_and_zero_content_are_not_successful_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path, out = base / 'old.txt', base / 'output'
            path.write_text('fictional source')
            for status in ('needs_dependency', 'unsupported', 'failed', 'empty', 'ok'):
                extracted = {'status': status, 'blocks': [], 'warnings': ['OCR dependency unavailable.']}
                with self.subTest(status=status), self.assertRaises(ValueError):
                    importer.import_files([path], out, extractor=lambda _: extracted)
                self.assertFalse(out.exists())

    def test_partial_source_keeps_missing_page_diagnostics_and_full_available_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path, out = base / 'old.pdf', base / 'output'
            path.write_bytes(b'fictional pdf stand-in for extractor unit test')
            extracted = extraction('Sample Candidate\nExperience\n- Built forms.')
            extracted.update(status='partial', warnings=['Page 2 needs OCR; text was not extracted.'], missing_pages=[2])
            importer.import_files([path], out, extractor=lambda _: extracted)
            source_data = json.loads((out / 'source.json').read_text())['sources'][0]['extraction']
            self.assertEqual(source_data['missing_pages'], [2])
            self.assertIn('Built forms', (out / 'resume.md').read_text())
            report = json.loads((out / 'import-report.json').read_text())
            self.assertEqual(report['source_statuses'][0]['status'], 'partial')
            self.assertTrue(any('Page 2' in warning for warning in report['warnings']))

    def test_structured_source_declared_language_precedes_text_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path, out = base / 'old.json', base / 'output'
            profile = json.loads((ROOT / 'assets/examples/en.json').read_text())
            profile['sections'][0]['entries'][0]['bullets'][0] = '保留原语言声明，内容可以混合语言。' * 60
            path.write_text(json.dumps(profile))
            extracted = {'structured_resume': profile, 'blocks': [
                {'id': f'field-{i}', 'text': text, 'locator': {'json_path': field}}
                for i, (field, text) in enumerate(importer.renderer.iter_visible_fields(profile))], 'warnings': []}
            importer.import_files([path], out, extractor=lambda _: extracted)
            self.assertEqual(json.loads((out / 'resume.json').read_text())['language'], 'en')
            self.assertEqual(json.loads((out / 'import-report.json').read_text())['language_method'],
                             'structured_source_declaration')

    def test_coaching_hook_can_write_review_without_modifying_fact_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path, out = base / 'old.txt', base / 'output'
            path.write_text(CHINESE)
            calls = []

            def coaching(data, evidence, report):
                calls.append((data['facts_confirmed'], evidence['facts'][0]['status']))
                return {'questions': ['这次投递哪个岗位？'], 'markdown': '# 待确认建议\n'}

            importer.import_files([path], out, extractor=lambda _: extraction(CHINESE), coaching=coaching)
            self.assertEqual(calls, [(False, 'source_only')])
            self.assertTrue((out / 'review.json').exists())
            self.assertIn('待确认建议', (out / 'review.md').read_text())


if __name__ == '__main__':
    unittest.main()
