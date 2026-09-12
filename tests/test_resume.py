"""Behavior checks for facts gates, escaping, templates and non-destructive output."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


def profile():
    data = resume.skeleton('software', 'zh')
    data.update(name='示例候选人', contacts=['sample@example.com'], facts_confirmed=True)
    data['sections'][0]['entries'] = [{'bullets': ['交付报表模板，由三个门店采用。']}]
    return data


class ResumeTests(unittest.TestCase):
    def test_all_templates_both_languages_produce_valid_drafts(self):
        for role in resume.ROLES:
            for language in ('en', 'zh'):
                data = resume.skeleton(role, language)
                resume.validate(data, draft=True)
                self.assertFalse(data['facts_confirmed'])
                self.assertEqual([s['id'] for s in data['sections']], resume.ROLES[role]['sections'])

    def test_unconfirmed_claims_block_final_but_allow_marked_draft(self):
        data = profile()
        data['facts_confirmed'] = False
        with self.assertRaisesRegex(ValueError, 'facts not confirmed'):
            resume.validate(data)
        resume.validate(data, draft=True)
        self.assertIn('DRAFT', resume.render_tex(data, draft=True))
        self.assertIn('DRAFT', resume.render_text(data, draft=True))

    def test_placeholders_cannot_enter_final(self):
        for placeholder in ('【待确认：收益】', 'YYYY.01', 'TBD'):
            data = profile()
            data['sections'][0]['entries'][0]['bullets'] = [placeholder]
            with self.assertRaisesRegex(ValueError, 'placeholder'):
                resume.validate(data)

    def test_structural_errors_have_clear_errors(self):
        for update in ({'contacts': []}, {'facts_confirmed': 'true'}, {'sections': []}, {'name': ''}):
            data = profile()
            data.update(update)
            with self.assertRaises(ValueError):
                resume.validate(data)

    def test_duplicate_sections_rejected(self):
        data = profile()
        data['sections'].append(copy.deepcopy(data['sections'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            resume.validate(data)

    def test_tex_commands_and_special_characters_are_literal(self):
        value = r'\input{secret} A&B 30% $5 #tag a_b ~ ^'
        escaped = resume.tex_escape(value)
        self.assertNotIn(r'\input{', escaped)
        self.assertIn(r'\textbackslash{}input\{secret\}', escaped)
        for expected in (r'A\&B', r'30\%', r'\$5', r'\#tag', r'a\_b'):
            self.assertIn(expected, escaped)

    def test_plain_theme_retains_content_without_icons(self):
        data = profile()
        plain = resume.render_tex(data, 'plain')
        self.assertNotIn(r'\faUser', plain)
        self.assertIn(data['sections'][0]['entries'][0]['bullets'][0], plain)
        self.assertIn(r'\faUser', resume.render_tex(data, 'classic'))

    def test_pdf_extraction_accepts_hyphenation_and_canonical_punctuation(self):
        expected = 'validation; no time-saving claim'
        extracted = 'vali-\n  dation\u037e no time-saving claim'
        self.assertIn(resume.normalized_text(expected), resume.extraction_variants(extracted))
        self.assertNotIn(resume.normalized_text('Revenue -5%'), resume.extraction_variants('Revenue 5%'))

    def test_cli_refuses_existing_output_without_altering_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / 'source.json'
            source.write_text(json.dumps(profile()))
            out = folder / 'out'
            out.mkdir()
            sentinel = out / 'resume.txt'
            sentinel.write_text('keep existing resume')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), 'render',
                                     str(source), '--out', str(out)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sentinel.read_text(), 'keep existing resume')


if __name__ == '__main__':
    unittest.main()
