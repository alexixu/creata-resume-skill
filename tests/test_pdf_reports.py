"""Automated PDF quality reports explain failures and locate affected source fields."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_pdf_reports', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)

PROFILE = {
    'name': 'Sample Candidate', 'headline': '', 'contacts': ['sample@example.com'],
    'sections': [
        {'title': 'Experience', 'entries': [
            {'heading': 'Fictional Company', 'date': '2024 - 2026',
             'bullets': ['Built a fictional reporting tool.']},
        ]},
    ],
}
EXTRACTED = ('Sample Candidate\nsample@example.com\nExperience\n'
             'Fictional Company\n2024 - 2026\nBuilt a fictional reporting tool.\n')


class PdfReportTests(unittest.TestCase):
    def build(self, *, pages=1, size='595.276 x 841.89', page_sizes=None, extracted=EXTRACTED,
              build_log='compile succeeded\n', max_pages=1):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'resume.json').write_text(json.dumps(PROFILE))
            (out / 'resume.tex').write_text('editable source')

            def run(command, cwd):
                if command[0] == 'tectonic':
                    generated = Path(command[command.index('--outdir') + 1])
                    (generated / 'resume.pdf').write_bytes(b'fictional mock PDF')
                    Path(command[command.index('--makefile-rules') + 1]).write_text('resume.pdf : resume.tex\n')
                    return build_log
                if command[0] == 'pdfinfo':
                    if '-f' in command:
                        return '\n'.join(f'Page {i} size: {dimensions} pts'
                                         for i, dimensions in enumerate(page_sizes or [size] * pages, 1))
                    return f'Pages: {pages}\nPage size: {size} pts\n'
                if command[0] == 'pdftotext':
                    return extracted
                self.fail(f'Unexpected external command: {command}')

            failure = None
            with patch.object(resume.shutil, 'which', return_value='/example/tool'), \
                    patch.object(resume, 'run', side_effect=run):
                try:
                    result = resume.build_pdf(out, max_pages)
                except ValueError as error:
                    failure = str(error)
                    result = None
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['max_pages'], max_pages)
            self.assertEqual(report['stage'], 'automated_checks')
            self.assertIn('automated_checks', report['completed_stages'])
            self.assertEqual(report['visual_review'], 'NOT_RUN')
            self.assertEqual(report['reading_order_review'], 'NOT_RUN')
            self.assertEqual(report['ats_compatibility'], 'NOT_CERTIFIED')
            self.assertEqual((out / 'resume.tex').read_text(), 'editable source')
            if failure is None:
                self.assertEqual(result, report)
                self.assertEqual(report['status'], 'PASSED')
                self.assertEqual(report['failure_reasons'], [])
            else:
                self.assertEqual(report['status'], 'FAILED')
                self.assertTrue(report['failure_reasons'])
                self.assertIn(report['error'], failure)
            return report, failure

    def test_success_has_explicit_automated_result_but_no_manual_approval(self):
        report, failure = self.build(pages=2, max_pages=2)
        self.assertIsNone(failure)
        self.assertTrue(report['within_page_limit'])
        self.assertEqual(report['missing_text_indices'], [])
        self.assertEqual(report['missing_text_fields'], [])

    def test_page_limit_failure_reports_actual_and_requested_counts(self):
        report, failure = self.build(pages=3)
        self.assertFalse(report['within_page_limit'])
        self.assertIn('3 pages (maximum 1)', failure)
        self.assertEqual(len(report['failure_reasons']), 1)

    def test_non_a4_failure_identifies_page_dimensions(self):
        report, failure = self.build(size='612 x 792')
        self.assertFalse(report['a4'])
        self.assertIn('not A4: 612 x 792 pt', failure)

    def test_second_page_dimensions_fail_with_page_location(self):
        report, failure = self.build(pages=2, max_pages=2,
                                     page_sizes=['595.276 x 841.89', '612 x 792'])
        self.assertEqual(report['non_a4_pages'], [2])
        self.assertTrue(report['page_sizes'][0]['a4'])
        self.assertFalse(report['page_sizes'][1]['a4'])
        self.assertIn('page 2 size is not A4', failure)

    def test_empty_extraction_reports_absent_text_and_missing_fields(self):
        report, failure = self.build(extracted='\n\f')
        self.assertFalse(report['text_present'])
        self.assertIn('no extractable text', failure)
        self.assertIn('name', report['missing_text_fields'])
        self.assertNotIn('headline', report['missing_text_fields'])

    def test_missing_text_retains_legacy_index_and_adds_json_field_path(self):
        report, failure = self.build(extracted=EXTRACTED.replace('Built a fictional reporting tool.\n', ''))
        field = 'sections[0].entries[0].bullets[0]'
        self.assertEqual(report['missing_text_indices'], [6])
        self.assertEqual(report['missing_text_fields'], [field])
        self.assertIn(field, failure)

    def test_duplicate_layout_warnings_count_once(self):
        warning = r'warning: resume.tex:25: Overfull \hbox (12pt too wide)'
        report, failure = self.build(build_log=f'{warning}\n{warning}\n')
        self.assertEqual(report['layout_warnings'], [warning])
        self.assertIn('1 layout or missing-character warning(s)', failure)


if __name__ == '__main__':
    unittest.main()
