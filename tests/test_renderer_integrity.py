"""A passing PDF QA must cover distinct fields, all pages and safe output writes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_integrity', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
PDF_TOOLS = all(shutil.which(name) for name in ('tectonic', 'pdfinfo', 'pdftotext'))
PROFILE = {'language': 'en', 'name': 'Fictional Candidate', 'headline': '',
           'facts_confirmed': True, 'contacts': ['sample@example.com'],
           'sections': [{'id': 'skills', 'title': 'Skills', 'entries': [
               {'heading': '', 'bullets': ['SQL', 'NoSQL databases']}]}]}


class TextCoverageTests(unittest.TestCase):
    def missing(self, fields, text):
        return resume.missing_text_indices(list(enumerate(fields)), text)

    def test_words_cannot_match_substrings_or_joined_whitespace(self):
        for expected, extracted in [('SQL', 'NoSQL databases'), ('SQL', 'SQLServer'),
                                    ('data base', 'database'), ('database', 'data base'),
                                    ('an API', 'a nAPI'), ('2024', '12024')]:
            with self.subTest(expected=expected, extracted=extracted):
                self.assertFalse(resume.text_is_present(expected, extracted))
        self.assertTrue(resume.text_is_present('SQL', 'Python, SQL, and C++'))

    def test_each_complete_field_needs_distinct_text(self):
        self.assertEqual(self.missing(['SQL', 'NoSQL databases'], 'NoSQL databases'), [0])
        self.assertEqual(self.missing(['Python', 'Python and SQL'], 'Python and SQL'), [0])
        self.assertEqual(self.missing(['Repeated claim.', 'Repeated claim.'], 'Repeated claim.'), [1])
        self.assertEqual(self.missing(['Repeated claim.', 'Repeated claim.'], 'Repeated claim.\nRepeated claim.'), [])
        self.assertEqual(self.missing(['Python', 'Python and SQL'], 'Python and SQL\nPython'), [])

    def test_complete_same_line_fields_take_precedence_over_cross_line_coincidence(self):
        self.assertEqual(self.missing(['Alice', 'B C', 'Alice B'], 'Alice\nB C\nAlice B'), [])
        # Removing the independent full contact must still fail coverage; the
        # opening name/headline cannot jointly stand in for that missing field.
        self.assertTrue(self.missing(['Alice', 'B C', 'Alice B'], 'Alice\nB C'))

    def test_source_whitespace_does_not_change_full_field_priority(self):
        self.assertEqual(self.missing(['SQL databases', '          SQL          '],
                                      'SQL databases\nSQL'), [])
        self.assertEqual(self.missing(['   Python         ', 'Python and SQL'],
                                      'Python and SQL\nPython'), [])

    def test_optional_cjk_spaces_do_not_outweigh_a_longer_complete_field(self):
        fields = ['数据分析项目', '数 据 分 析']
        self.assertEqual(self.missing(fields, '数据分析项目\n数 据 分 析'), [])
        self.assertEqual(self.missing(fields, '数据分析项目\n数据分析'), [])
        self.assertEqual(self.missing(fields, '数据分析项目'), [1])
        self.assertEqual(self.missing(fields, '数 据 分 析'), [0])

    def test_only_single_capital_initials_may_lose_spaces(self):
        for expected, text in [('B C', 'BC'), ('J P Smith', 'JP Smith'), ('J P Smith', 'J P Smith')]:
            with self.subTest(expected=expected, extracted=text):
                self.assertTrue(resume.text_is_present(expected, text))
        for expected, text in [('B C', 'XBC'), ('B C', 'BCX'), ('J P Smith', 'JPSmith'),
                               ('Alice Build', 'AliceBuild'), ('SQL NoSQL', 'SQLNoSQL'),
                               ('data base', 'database'), ('b c', 'bc')]:
            with self.subTest(expected=expected, extracted=text):
                self.assertFalse(resume.text_is_present(expected, text))
        self.assertTrue(self.missing(['B C', 'B C'], 'BC'))

    def test_legitimate_wrapping_and_unicode_remain_supported(self):
        for expected, extracted in [
            ('in-house validation', 'in-\nhouse vali-\ndation'),
            ('Built reliable forms.', 'Built reliable\n  forms.'),
            ('整理门店报表，支持 SQL 查询。', '整理门店报\n  表，支持 SQL 查询。'),
            ('ﬁeld validation;', 'field vali-\ndation\u037e'),
            ('https://example.com/a_b?q=10%&value={x}#ref',
             'https://example.com/a_\n b?q=10%&value=\n {x}#ref'),
        ]:
            with self.subTest(expected=expected):
                self.assertTrue(resume.text_is_present(expected, extracted))

    def test_em_dash_gaps_may_vanish_without_losing_words_or_signs(self):
        headline = 'Operations Support \u2014 Direction Draft'
        for text in ('Operations Support \u2014Direction Draft',
                     'Operations Support\u2014 Direction Draft',
                     'Operations Support\u2014Direction Draft'):
            with self.subTest(extracted=text):
                self.assertTrue(resume.text_is_present(headline, text))
        for text in ('Operations Support Direction Draft',
                     'OperationsSupport\u2014Direction Draft',
                     'Operations Support\u2014DirectionDraft'):
            with self.subTest(extracted=text):
                self.assertFalse(resume.text_is_present(headline, text))
        self.assertFalse(resume.text_is_present('Reduced cost by -30%.', 'Reduced cost by 30%.'))
        self.assertEqual(self.missing([headline, 'Direction Draft'],
                                      'Operations Support\u2014Direction Draft'), [1])
        self.assertEqual(self.missing([headline, headline],
                                      'Operations Support\u2014Direction Draft'), [1])


class OutputSafetyTests(unittest.TestCase):
    def test_generated_symlinks_are_refused_before_any_external_command(self):
        for compile_pdf in (False, True):
            for name in resume.GENERATED_FILES:
                with self.subTest(compile=compile_pdf, file=name), tempfile.TemporaryDirectory() as tmp:
                    folder = Path(tmp)
                    out = folder / 'output'
                    out.mkdir()
                    sentinel = folder / 'outside-private-file'
                    sentinel.write_bytes(b'private sentinel, preserve exactly')
                    (out / name).symlink_to(sentinel)
                    with patch.object(resume, 'run') as run:
                        with self.assertRaisesRegex(ValueError, 'unsafe generated target ' + name):
                            resume.build_pdf(out, 1, compile_pdf=compile_pdf)
                    run.assert_not_called()
                    self.assertEqual(sentinel.read_bytes(), b'private sentinel, preserve exactly')
                    self.assertTrue((out / name).is_symlink())

    def test_dangling_symlinks_and_directories_are_not_report_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            target = folder / 'qa.json'
            target.symlink_to(folder / 'missing')
            with self.assertRaisesRegex(ValueError, 'unsafe generated target'):
                resume.safe_write(target, 'report')
            self.assertFalse((folder / 'missing').exists())
            target.unlink()
            target.mkdir()
            with self.assertRaisesRegex(ValueError, 'unsafe generated target'):
                resume.safe_write(target, 'report')

    def test_atomic_report_replacement_does_not_write_through_hardlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            sentinel, report = folder / 'private', folder / 'qa.json'
            sentinel.write_text('original private content')
            os.link(sentinel, report)
            resume.safe_write(report, 'new QA report')
            self.assertEqual(sentinel.read_text(), 'original private content')
            self.assertEqual(report.read_text(), 'new QA report')


@unittest.skipUnless(PDF_TOOLS, 'requires tectonic and Poppler')
class RealIntegrityTests(unittest.TestCase):
    def test_em_dash_headline_passes_both_themes_without_hiding_missing_fields(self):
        headline = 'Process Operations / Product Operations Support \u2014 Direction Draft'
        data = {'language': 'en', 'name': 'Fictional Candidate', 'headline': headline,
                'facts_confirmed': True, 'contacts': ['sample@example.com'],
                'sections': [{'id': 'skills', 'title': 'Skills', 'entries': [
                    {'bullets': ['Reduced cost by -30%.', 'Direction Draft']}]}]}
        with tempfile.TemporaryDirectory(prefix='resume-em-dash-') as tmp:
            folder = Path(tmp)
            source = folder / 'fictional.json'
            source.write_text(json.dumps(data, ensure_ascii=False))
            def cli(*args):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), *map(str, args)],
                                      capture_output=True, text=True)
            for theme in ('plain', 'classic'):
                with self.subTest(theme=theme):
                    out = folder / theme
                    built = cli('render', source, '--out', out, '--theme', theme, '--pdf')
                    self.assertEqual(built.returncode, 0, built.stderr)
                    self.assertEqual(json.loads((out / 'qa.json').read_text())['missing_text_fields'], [])
                    self.assertTrue(resume.text_is_present(headline, (out / 'resume-extracted.txt').read_text()))
                    self.assertEqual(cli('check', out).returncode, 0)
                    tex = out / 'resume.tex'
                    original = tex.read_text()

                    # The headline's embedded words cannot replace an omitted
                    # independent field, even with optional dash spacing.
                    private = original.replace('\\item Direction Draft\n', '')
                    self.assertNotEqual(private, original)
                    tex.write_text(private)
                    rebuilt = cli('rebuild', out)
                    self.assertEqual(rebuilt.returncode, 1, rebuilt.stdout)
                    self.assertEqual(json.loads((out / 'qa.json').read_text())['missing_text_fields'],
                                     ['sections[0].entries[0].bullets[1]'])
                    self.assertEqual(cli('check', out).returncode, 1)
                    self.assertEqual(tex.read_text(), private)

                    # Optional whitespace must not make either the literal em
                    # dash or a quantitative claim's negative sign optional.
                    private = original.replace(headline, headline.replace(' \u2014 ', ' ')).replace('-30', '30')
                    tex.write_text(private)
                    rebuilt = cli('rebuild', out)
                    self.assertEqual(rebuilt.returncode, 1, rebuilt.stdout)
                    self.assertEqual(json.loads((out / 'qa.json').read_text())['missing_text_fields'],
                                     ['headline', 'sections[0].entries[0].bullets[0]'])
                    self.assertEqual(cli('check', out).returncode, 1)
                    self.assertEqual(tex.read_text(), private)

    def test_cjk_spacing_priority_preserves_both_real_pdf_fields(self):
        data = {'language': 'zh', 'name': '虚构候选人', 'headline': '',
                'facts_confirmed': True, 'contacts': ['sample@example.com'],
                'sections': [{'id': 'skills', 'title': '专业能力', 'entries': [
                    {'bullets': ['数据分析项目', '数 据 分 析']}]}]}
        with tempfile.TemporaryDirectory(prefix='resume-cjk-coverage-') as tmp:
            folder = Path(tmp)
            source, out = folder / 'fictional.json', folder / 'pdf'
            source.write_text(json.dumps(data, ensure_ascii=False))
            built = subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'),
                                    'render', str(source), '--out', str(out), '--pdf'],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['missing_text_fields'], [])
            self.assertEqual(report['status'], 'PASSED')
            text = (out / 'resume-extracted.txt').read_text()
            self.assertIn('数据分析项目', text)
            self.assertTrue(resume.text_is_present('数 据 分 析', text))

    def test_edited_pdf_cannot_hide_sql_in_nosql_and_all_pages_are_checked(self):
        with tempfile.TemporaryDirectory(prefix='resume-integrity-') as tmp:
            folder = Path(tmp)
            source, out = folder / 'fictional.json', folder / 'output'
            source.write_text(json.dumps(PROFILE))
            def cli(*args):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), *map(str, args)],
                                      capture_output=True, text=True)
            initial = cli('render', source, '--out', out, '--pdf')
            self.assertEqual(initial.returncode, 0, initial.stderr)
            tex = out / 'resume.tex'
            original = tex.read_text()
            private = original.replace('\\item SQL\n', '')
            self.assertNotEqual(private, original)
            tex.write_text(private)
            rebuilt = cli('rebuild', out)
            self.assertEqual(rebuilt.returncode, 1, rebuilt.stdout)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['missing_text_fields'], ['sections[0].entries[0].bullets[0]'])
            self.assertEqual(tex.read_text(), private)
            checked = cli('check', out)
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn('missing text at', checked.stderr)

            # A private extra page is allowed by --max-pages, but must still be A4.
            tex.write_text(original.replace('\\end{document}',
                '\\newpage\n\\special{papersize=612pt,792pt}\nPrivate second page.\n\\end{document}'))
            mixed = cli('rebuild', out, '--max-pages', '2')
            self.assertEqual(mixed.returncode, 1, mixed.stdout)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['pages'], 2)
            self.assertEqual(report['non_a4_pages'], [2])
            self.assertIn('page 2 size is not A4', mixed.stderr)
            self.assertEqual(report['missing_text_fields'], [])
            checked = cli('check', out, '--max-pages', '2')
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn('page 2 size is not A4', checked.stderr)

            # The same actual output directory must reject report/compiler links.
            sentinel = folder / 'external.txt'
            sentinel.write_text('private sentinel')
            for command, name in [('check', 'qa.json'), ('rebuild', 'build.log'),
                                  ('rebuild', 'resume.pdf'), ('rebuild', 'build-dependencies.mk'),
                                  ('check', 'resume-extracted.txt')]:
                with self.subTest(command=command, filename=name):
                    path = out / name
                    saved = path.read_bytes()
                    path.unlink()
                    path.symlink_to(sentinel)
                    rejected = cli(command, out, '--max-pages', '2')
                    self.assertEqual(rejected.returncode, 1)
                    self.assertIn('unsafe generated target', rejected.stderr)
                    self.assertEqual(sentinel.read_text(), 'private sentinel')
                    path.unlink()
                    path.write_bytes(saved)

            # Literal source whitespace is normalized by the renderer. It must
            # not let a short padded field take priority over a complete phrase.
            data = json.loads(json.dumps(PROFILE))
            data['sections'][0]['entries'][0]['bullets'] = ['SQL databases', '          SQL          ']
            source.write_text(json.dumps(data))
            spaced = folder / 'spaces'
            built = cli('render', source, '--out', spaced, '--pdf')
            self.assertEqual(built.returncode, 0, built.stderr)
            report = json.loads((spaced / 'qa.json').read_text())
            self.assertEqual(report['missing_text_fields'], [])
            self.assertEqual(report['status'], 'PASSED')

            # Private order plus Poppler's narrow initial spacing: all three
            # header fields are visible and need their own text spans.
            data.update(name='Alice', headline='B C', contacts=['Alice B'])
            (spaced / 'resume.json').write_text(json.dumps(data))
            private_tex = ('\\documentclass[literaltext]{resume}\n\\begin{document}\n'
                           '\\pagenumbering{gobble}\nAlice\\par\nB C\\par\nAlice B\\par\n'
                           '\\section{Skills}\n\\begin{itemize}\n\\item SQL databases\n'
                           '\\item SQL\n\\end{itemize}\n\\end{document}\n')
            (spaced / 'resume.tex').write_text(private_tex)
            initials = cli('rebuild', spaced)
            self.assertEqual(initials.returncode, 0, initials.stderr)
            self.assertEqual((spaced / 'resume.tex').read_text(), private_tex)
            self.assertEqual(json.loads((spaced / 'qa.json').read_text())['missing_text_fields'], [])


if __name__ == '__main__':
    unittest.main()
