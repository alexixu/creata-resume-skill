"""Real-PDF regression checks; skipped when the optional PDF tools are absent."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
PDF_TOOLS = all(shutil.which(tool) for tool in ('tectonic', 'pdfinfo', 'pdftotext'))


@unittest.skipUnless(PDF_TOOLS, 'requires tectonic and Poppler')
class PDFLayoutTests(unittest.TestCase):
    def test_long_name_and_literal_punctuation_fit_page(self):
        data = {
            'language': 'en', 'role': 'software', 'facts_confirmed': True,
            'name': 'Alexandria Catherine Montgomery Wellington Henderson Sample Candidate',
            'headline': 'Fictional layout regression example',
            'contacts': ['sample@example.com'],
            'sections': [{'id': 'experience', 'title': 'Experience', 'entries': [{
                'heading': "Example Company / O'Brien's Workflow Team", 'date': '2023 - 2026',
                'bullets': ["Ran --dry-run and --verbose on customers' reports; revenue -5%."]
            }]}]
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
                         '--out', str(out), '--theme', theme, '--pdf'],
                        capture_output=True, text=True)
                    diagnostics = result.stdout + result.stderr
                    if result.returncode:
                        for filename in ('qa.json', 'build.log'):
                            if (out / filename).exists():
                                diagnostics += (out / filename).read_text()
                    self.assertEqual(result.returncode, 0, diagnostics)
                    qa = json.loads((out / 'qa.json').read_text())
                    self.assertEqual(qa['pages'], 1)
                    self.assertTrue(qa['a4'])
                    self.assertEqual(qa['missing_text_indices'], [])
                    self.assertEqual(qa['layout_warnings'], [])
                    self.assertEqual(qa['visual_review'], 'NOT_RUN')
                    extracted = (out / 'resume-extracted.txt').read_text()
                    for literal in ("O'Brien's", "customers'", '--dry-run', '--verbose', '-5%'):
                        self.assertIn(literal, extracted)
                    # Presence alone cannot detect text protruding into the margins.
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
