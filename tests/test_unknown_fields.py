"""Misspelled input keys cannot silently remove confirmed resume content."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_unknown_fields', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


def profile():
    return json.loads((ROOT / 'assets/examples/en.json').read_text())


class UnknownFieldTests(unittest.TestCase):
    def test_unknown_fields_are_rejected_at_every_object_level_even_in_drafts(self):
        cases = [([], 'contact', 'contact'), (['sections', 0], 'entry', 'sections[0].entry'),
                 (['sections', 2, 'entries', 0], 'bullet', 'sections[2].entries[0].bullet')]
        for keys, typo, expected in cases:
            for draft in (False, True):
                with self.subTest(path=expected, draft=draft):
                    data = profile()
                    parent = data
                    for key in keys:
                        parent = parent[key]
                    parent[typo] = ['confirmed fictional content']
                    with self.assertRaises(ValueError) as caught:
                        resume.validate(data, draft=draft)
                    self.assertIn(expected + ': unknown field', str(caught.exception))

    def test_role_is_optional_but_present_values_must_be_known(self):
        data = profile()
        del data['role']
        resume.validate(data)
        for value in (None, [], '', 'sofware'):
            data['role'] = value
            with self.subTest(role=value), self.assertRaisesRegex(ValueError, 'role'):
                resume.validate(data)

    def test_fictional_notice_and_custom_sections_remain_compatible(self):
        for language in ('en', 'zh'):
            data = json.loads((ROOT / 'assets/examples' / f'{language}.json').read_text())
            data['sections'].append({'id': 'community', 'title': 'Community', 'entries': []})
            resume.validate(data)
        data['example_notice'] = []
        with self.assertRaisesRegex(ValueError, 'example_notice'):
            resume.validate(data)

    def test_misspelled_bullets_fail_before_any_output_is_written(self):
        data = profile()
        entry = data['sections'][2]['entries'][0]
        entry['bullet'] = entry.pop('bullets')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'profile.json'
            source.write_text(json.dumps(data))
            out = folder / 'out'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), 'render',
                                     str(source), '--out', str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('sections[2].entries[0].bullet: unknown field', result.stderr)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
