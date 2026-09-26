"""User-facing input errors locate the source without rejecting ordinary text."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_validation', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


def profile():
    return json.loads((ROOT / 'assets/examples/zh.json').read_text())


class InputValidationTests(unittest.TestCase):
    def test_ordinary_chinese_brackets_are_literal_content(self):
        data = profile()
        value = '参与【搜索体验】专项，整理搜索功能需求。'
        data['sections'][0]['entries'][0]['bullets'] = [value]
        resume.validate(data)
        self.assertIn(value, resume.render_text(data))
        self.assertIn(value, resume.render_tex(data))

    def test_shipped_role_recipes_still_cannot_be_delivered_unfilled(self):
        for role, recipe in resume.ROLES.items():
            with self.subTest(role=role):
                data = profile()
                data['sections'][0]['entries'][0]['bullets'] = [recipe['bullet_pattern']]
                with self.assertRaisesRegex(ValueError, 'unresolved placeholder'):
                    resume.validate(data)
                resume.validate(data, draft=True)

    def test_omitted_section_does_not_block_final_but_visible_placeholder_does(self):
        data = profile()
        data['sections'].append({'id': 'optional', 'title': '【待确认：补充资料】', 'entries': []})
        resume.validate(data)
        self.assertNotIn('待确认', resume.render_text(data))
        data['sections'][-1]['entries'] = [{'bullets': ['已交付公开演示。']}]
        with self.assertRaisesRegex(ValueError, r'sections\[5\]\.title: unresolved placeholder'):
            resume.validate(data)

    def test_nested_errors_include_exact_source_path(self):
        cases = (
            (['contacts', 1], 42, 'contacts[1]'),
            (['sections', 1], [], 'sections[1]'),
            (['sections', 1, 'entries'], {}, 'sections[1].entries'),
            (['sections', 1, 'entries', 1], None, 'sections[1].entries[1]'),
            (['sections', 1, 'entries', 1, 'heading'], [], 'sections[1].entries[1].heading'),
            (['sections', 1, 'entries', 1, 'date'], 2026, 'sections[1].entries[1].date'),
            (['sections', 1, 'entries', 1, 'bullets'], 'text', 'sections[1].entries[1].bullets'),
            (['sections', 1, 'entries', 1, 'bullets', 1], 42, 'sections[1].entries[1].bullets[1]'),
            (['sections', 1, 'entries', 1, 'bullets', 1], '【待补充：事实】',
             'sections[1].entries[1].bullets[1]'),
        )
        for keys, value, expected_path in cases:
            with self.subTest(path=expected_path, value=value):
                data = copy.deepcopy(profile())
                parent = data
                for key in keys[:-1]:
                    parent = parent[key]
                parent[keys[-1]] = value
                with self.assertRaises(ValueError) as caught:
                    resume.validate(data)
                self.assertIn(expected_path, str(caught.exception))

    def test_windows_line_endings_are_normalized_like_other_whitespace(self):
        data = profile()
        data['sections'][0]['entries'][0]['bullets'] = ['整理需求清单\r\n并维护帮助文档。']
        resume.validate(data)
        self.assertIn('整理需求清单 并维护帮助文档。', resume.render_text(data))
        self.assertNotIn('\r', resume.render_tex(data))
        data['sections'][0]['entries'][0]['bullets'] = ['invalid\x00text']
        with self.assertRaisesRegex(ValueError, 'unsupported control characters'):
            resume.validate(data)

    def test_cli_reports_input_location_before_creating_output(self):
        data = profile()
        data['sections'][1]['entries'][1]['bullets'][1] = 42
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'profile.json'
            source.write_text(json.dumps(data))
            out = folder / 'out'
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/resume.py'), 'render', str(source), '--out', str(out)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('sections[1].entries[1].bullets[1]', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
