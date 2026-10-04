"""Numeric tokens and ordinary en-dash dates survive faithful PDF extraction."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_numeric_integrity', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
PDF_TOOLS = all(shutil.which(name) for name in ('tectonic', 'pdfinfo', 'pdftotext'))


class NumericTextTests(unittest.TestCase):
    def test_numeric_prefixes_suffixes_and_sign_changes_are_not_complete_values(self):
        for expected, extracted in [
            ('Supported stores: 3', 'Supported stores: 3,000'),
            ('GPA: 3', 'GPA: 3.9'), ('GPA: 3.', 'GPA: 3.9.'),
            ('Count: 3,', 'Count: 3,000'), ('2024', '2024.06'),
            ('3', '13'), ('3', '0.3'), ('3', '.3'), ('3', '1,003'),
            ('003', '1,003'), ('3.9', '13.9'), ('3.9', '3.99'),
            ('5% improvement', '-5% improvement'), ('5% improvement', '\u22125% improvement'),
            ('5% improvement', '+5% improvement'), ('-3', '3'),
            ('-3', '1-3'), ('-3', '--3'), ('3', '1e-3'),
            ('3%', '13%'), ('3%', '3.9%'),
        ]:
            with self.subTest(expected=expected, extracted=extracted):
                self.assertFalse(resume.text_is_present(expected, extracted))

    def test_original_values_dates_and_sentence_punctuation_remain_valid(self):
        for expected, extracted in [
            ('Supported stores: 3', 'Supported stores: 3.'),
            ('Supported stores: 3.', 'Supported stores: 3.'),
            ('Count: 3', 'Count: 3, plus an internal test.'),
            ('Count: 3,', 'Count: 3, plus an internal test.'),
            ('GPA: 3.9', 'GPA: 3.9.'), ('GPA: 3.9.', 'GPA: 3.9.'),
            ('1,003', '1,003'), ('3.9', '3.9'), ('.3', '.3'),
            ('-3', '-3'), ('\u22123', '\u22123'), ('+3', '+3'),
            ('5% improvement', '5% improvement'), ('IPv6', 'IPv6'),
            ('Python 3', 'Python 3'), ('2023.07 – 2026.01', '2023.07 –2026.01'),
            ('Sep 2023 – Jan 2026', 'Sep 2023 –Jan 2026'),
            ('2023-2026', '2023-2026'),
        ]:
            with self.subTest(expected=expected, extracted=extracted):
                self.assertTrue(resume.text_is_present(expected, extracted))

    def test_dash_spacing_does_not_erase_dashes_signs_or_ordinary_word_spaces(self):
        for dash in ('\u2013', '\u2014'):
            source = f'Software Engineer {dash} Backend'
            self.assertTrue(resume.text_is_present(source, f'Software Engineer {dash}Backend'))
            self.assertFalse(resume.text_is_present(source, 'Software Engineer Backend'))
            self.assertFalse(resume.text_is_present(source, f'SoftwareEngineer {dash}Backend'))
            self.assertFalse(resume.text_is_present(source, 'Software Engineer - Backend'))
            self.assertFalse(resume.text_is_present('5% improvement', f'{dash}5% improvement'))
        self.assertFalse(resume.text_is_present('Revenue -5%', 'Revenue 5%'))
        self.assertFalse(resume.text_is_present('data base', 'database'))

    def test_only_observed_right_curly_quote_gaps_may_be_omitted(self):
        for source, extracted in [
            ('Author of “Planning & Delivery” documentation.',
             'Author of “Planning & Delivery”documentation.'),
            ('Documented ‘review steps’ for teams.', 'Documented ‘review steps’for teams.'),
        ]:
            with self.subTest(source=source):
                self.assertTrue(resume.text_is_present(source, extracted))
                self.assertTrue(resume.text_is_present(source, source))
                self.assertFalse(resume.text_is_present(source, extracted.replace('”', '').replace('’', '')))
                self.assertFalse(resume.text_is_present(source, extracted.replace('“', '').replace('‘', '')))
        source = 'Author of “Planning & Delivery” documentation.'
        for changed in ('Authorof “Planning & Delivery”documentation.',
                        'Author of“Planning & Delivery”documentation.',
                        'Author of “Planning&Delivery”documentation.',
                        'Author of “Planning & Delivery"documentation.'):
            self.assertFalse(resume.text_is_present(source, changed))
        self.assertFalse(resume.text_is_present('Built "forms" for teams.', 'Built "forms"for teams.'))
        self.assertFalse(resume.text_is_present("Built 'forms' for teams.", "Built 'forms'for teams."))


@unittest.skipUnless(PDF_TOOLS, 'requires tectonic and Poppler')
class RealNumericTextTests(unittest.TestCase):
    def test_both_themes_keep_curly_quotes_and_reject_deleted_quotes(self):
        data = {'language': 'en', 'name': 'Fictional Candidate', 'headline': 'Software Engineer',
                'facts_confirmed': True, 'contacts': ['fictional@example.invalid'], 'sections': [
                    {'id': 'skills', 'title': 'Skills', 'entries': [{'bullets': [
                        'Author of “Planning & Delivery” documentation.',
                        'Documented ‘review steps’ for teams.']}]}]}
        with tempfile.TemporaryDirectory(prefix='resume-quote-qa-') as tmp:
            folder = Path(tmp)
            source = folder / 'fictional.json'
            source.write_text(json.dumps(data, ensure_ascii=False))
            before = hashlib.sha256(source.read_bytes()).hexdigest()

            def cli(*args):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), *map(str, args)],
                                      capture_output=True, text=True)

            for theme in ('classic', 'plain'):
                with self.subTest(theme=theme):
                    out = folder / theme
                    built = cli('render', source, '--out', out, '--theme', theme, '--pdf')
                    self.assertEqual(built.returncode, 0, built.stderr)
                    self.assertEqual(json.loads((out / 'qa.json').read_text())['missing_text_fields'], [])
                    extracted = (out / 'resume-extracted.txt').read_text()
                    self.assertIn('“Planning & Delivery”', extracted)
                    self.assertIn('‘review steps’', extracted)
                    changed = (out / 'resume.tex').read_text().replace('”', '').replace('’', '')
                    original_json = (out / 'resume.json').read_bytes()
                    (out / 'resume.tex').write_text(changed)
                    for command in ('rebuild', 'check'):
                        failed = cli(command, out)
                        self.assertEqual(failed.returncode, 1, failed.stdout)
                        qa = json.loads((out / 'qa.json').read_text())
                        self.assertEqual(qa['missing_text_fields'], [
                            'sections[0].entries[0].bullets[0]', 'sections[0].entries[0].bullets[1]'])
                        self.assertEqual((out / 'resume.tex').read_text(), changed)
                        self.assertEqual((out / 'resume.json').read_bytes(), original_json)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_both_themes_preserve_en_dash_and_reject_private_numeric_edits(self):
        data = {'language': 'en', 'name': 'Fictional Candidate',
                'headline': 'Software Engineer – Backend', 'facts_confirmed': True,
                'contacts': ['sample@example.com'], 'sections': [
                    {'id': 'experience', 'title': 'Experience', 'entries': [
                        {'heading': 'Example Company', 'date': 'Sep 2023 – Jan 2026',
                         'bullets': ['Supported stores: 3', 'Median review time: 3.',
                                     '5% improvement in sample throughput',
                                     'Handled 1,003 fictional records; GPA example: 3.9.']}]}]}
        with tempfile.TemporaryDirectory(prefix='resume-numeric-qa-') as tmp:
            folder = Path(tmp)
            source = folder / 'fictional.json'
            source.write_text(json.dumps(data, ensure_ascii=False))
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            def cli(*args):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), *map(str, args)],
                                      capture_output=True, text=True)

            for theme in ('classic', 'plain'):
                with self.subTest(theme=theme):
                    out = folder / theme
                    built = cli('render', source, '--out', out, '--theme', theme, '--pdf')
                    self.assertEqual(built.returncode, 0, built.stderr)
                    self.assertEqual(json.loads((out / 'qa.json').read_text())['status'], 'PASSED')
                    original = (out / 'resume.tex').read_text()
                    original_json = (out / 'resume.json').read_bytes()
                    changed = original.replace('Supported stores: 3', 'Supported stores: 3,000')
                    changed = changed.replace('Median review time: 3.', 'Median review time: 3.9.')
                    changed = changed.replace(r'\item 5\%', r'\item -5\%')
                    self.assertNotEqual(changed, original)
                    (out / 'resume.tex').write_text(changed)
                    expected_missing = [f'sections[0].entries[0].bullets[{i}]' for i in range(3)]
                    for command in ('rebuild', 'check'):
                        failed = cli(command, out)
                        self.assertEqual(failed.returncode, 1, failed.stdout)
                        qa = json.loads((out / 'qa.json').read_text())
                        self.assertEqual(qa['status'], 'FAILED')
                        self.assertEqual(qa['missing_text_fields'], expected_missing)
                        self.assertEqual((out / 'resume.tex').read_text(), changed)
                        self.assertEqual((out / 'resume.json').read_bytes(), original_json)
                    # Omitting either en dash changes the original visible text.
                    without_dash = original.replace('Software Engineer – Backend', 'Software Engineer Backend')
                    without_dash = without_dash.replace('Sep 2023 – Jan 2026', 'Sep 2023 Jan 2026')
                    (out / 'resume.tex').write_text(without_dash)
                    failed = cli('rebuild', out)
                    self.assertEqual(failed.returncode, 1, failed.stdout)
                    qa = json.loads((out / 'qa.json').read_text())
                    self.assertEqual(qa['missing_text_fields'], ['headline', 'sections[0].entries[0].date'])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)


if __name__ == '__main__':
    unittest.main()
