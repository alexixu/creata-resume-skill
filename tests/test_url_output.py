"""Long links wrap without changing their literal content or page boundaries."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_urls', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
PDF_TOOLS = all(shutil.which(tool) for tool in ('tectonic', 'pdfinfo', 'pdftotext'))


class UrlOutputTests(unittest.TestCase):
    def test_text_without_links_keeps_existing_escaping(self):
        self.assertEqual(resume.tex_escape('  C++ & Python\n  10% growth '), r'C++ \& Python 10\% growth')
        self.assertEqual(resume.tex_escape(r'\input{fictional}'), r'\textbackslash{}input\{fictional\}')

    @unittest.skipUnless(PDF_TOOLS, 'requires tectonic and Poppler')
    def test_long_links_keep_all_characters_and_fit_both_themes(self):
        slug = 'abcdefghijkmnpqrstuvwxyz23456789' * 5
        contact = 'Portfolio: www.example.com/' + slug
        links = [
            'Reference: https://example.com/' + slug,
            '作品https://example.com/' + slug + '?q=a_b&discount=10%#literal{value}',
            'Reference: example.com/' + slug,
            r'Literal: https://example.com/\input{fictional}/$^~|',
        ]
        data = {
            'name': 'Sample Candidate', 'headline': 'Fictional URL layout example',
            'language': 'en', 'role': 'software', 'facts_confirmed': True,
            'contacts': [contact],
            'sections': [{'id': 'projects', 'title': 'Projects',
                          'entries': [{'heading': 'Fictional project', 'bullets': links}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'profile.json'
            source.write_text(json.dumps(data), encoding='utf-8')
            for theme in ('classic', 'plain'):
                with self.subTest(theme=theme):
                    out = folder / theme
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'scripts/resume.py'), 'render', str(source),
                         '--out', str(out), '--theme', theme, '--pdf'], capture_output=True, text=True)
                    diagnostics = result.stdout + result.stderr
                    for name in ('qa.json', 'build.log'):
                        if result.returncode and (out / name).exists():
                            diagnostics += (out / name).read_text()
                    self.assertEqual(result.returncode, 0, diagnostics)
                    qa = json.loads((out / 'qa.json').read_text())
                    self.assertEqual(qa['pages'], 1)
                    self.assertEqual(qa['status'], 'PASSED')
                    self.assertEqual(qa['layout_warnings'], [])
                    extracted = (out / 'resume-extracted.txt').read_text()
                    # Check exact characters independently of the QA's hyphenation matcher.
                    compact = ''.join(extracted.split())
                    for value in (contact, *links):
                        self.assertIn(''.join(value.split()), compact)
                    self.assertIn('Reference: ', extracted)
                    bbox = subprocess.run(
                        ['pdftotext', '-bbox', str(out / 'resume.pdf'), '-'],
                        check=True, capture_output=True, text=True).stdout
                    words = ET.fromstring(bbox).findall('.//{*}word')
                    self.assertTrue(words)
                    for word in words:
                        self.assertGreaterEqual(float(word.attrib['xMin']), 51.84 - 1, word.text)
                        self.assertLessEqual(float(word.attrib['xMax']), 595.276 - 51.84 + 1, word.text)


if __name__ == '__main__':
    unittest.main()
