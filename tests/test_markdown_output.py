"""Portable checks for literal Markdown content and explicit header line breaks."""
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


def profile():
    return {
        'language': 'en', 'role': 'software', 'name': 'Fictional Candidate',
        'headline': 'Software Engineer',
        'contacts': ['sample@example.com', '+1 202 555 0100', 'https://portfolio.example'],
        'facts_confirmed': True,
        'sections': [{'id': 'projects', 'title': 'Projects', 'entries': [{
            'heading': 'Search Project',
            'bullets': ['Documented the search feature requirements.']
        }]}]
    }


class MarkdownOutputTests(unittest.TestCase):
    def test_entity_spellings_remain_literal_in_all_visible_fields(self):
        data = profile()
        data.update(name='Candidate &copy;', headline='Engineer &amp;', contacts=['sample &nbsp; contact'])
        section = data['sections'][0]
        section['title'] = 'Projects &lt;'
        section['entries'][0].update(heading='Editor &quot;', date='2024 &gt;',
                                     bullets=['Displayed &copy; and &#65; as literal text.'])
        markdown = resume.render_text(data, markdown=True)
        for entity in ('copy', 'amp', 'nbsp', 'lt', 'quot', 'gt'):
            with self.subTest(entity=entity):
                self.assertIn('\\&' + entity + ';', markdown)
        self.assertIn(r'\&\#65;', markdown)

    def test_entry_headings_cannot_become_lists_or_thematic_breaks(self):
        examples = (
            ('2024. Search launch', r'2024\. Search launch'),
            ('1) Search launch', r'1\) Search launch'),
            ('- Internal search project', r'\- Internal search project'),
            ('+ Internal search project', r'\+ Internal search project'),
            ('---', r'\-\-\-'),
            ('===', r'\=\=\='),
        )
        for heading, escaped in examples:
            with self.subTest(heading=heading):
                data = profile()
                data['sections'][0]['entries'][0]['heading'] = heading
                markdown = resume.render_text(data, markdown=True)
                self.assertIn('\n\n' + escaped + '\n- ', markdown)
                self.assertEqual(sum(line.startswith('- ') for line in markdown.splitlines()), 1)

    def test_bullet_content_keeps_its_literal_prefix_and_punctuation(self):
        data = profile()
        data['sections'][0]['entries'][0]['bullets'] = ['- option --dry-run &copy;', '1. First release']
        markdown = resume.render_text(data, markdown=True)
        self.assertIn(r'- \- option \-\-dry\-run \&copy;', markdown)
        self.assertIn(r'- 1\. First release', markdown)
        self.assertEqual(sum(line.startswith('- ') for line in markdown.splitlines()), 2)

    def test_headline_and_contacts_have_explicit_markdown_line_breaks(self):
        markdown = resume.render_text(profile(), markdown=True)
        self.assertTrue(markdown.startswith(
            '# Fictional Candidate\nSoftware Engineer  \n'
            'sample@example\\.com  \n\\+1 202 555 0100  \n'
            'https://portfolio\\.example\n\n## Projects\n'))

    def test_plain_text_stays_unescaped_with_original_line_structure(self):
        data = profile()
        data['sections'][0]['entries'][0].update(heading='2024. Search &copy;',
                                                bullets=['- option --dry-run &copy;'])
        self.assertEqual(resume.render_text(data),
                         'Fictional Candidate\nSoftware Engineer\nsample@example.com\n'
                         '+1 202 555 0100\nhttps://portfolio.example\n\nProjects\n\n'
                         '2024. Search &copy;\n- - option --dry-run &copy;\n')

    def test_unnamed_draft_notice_and_empty_header_remain_valid(self):
        data = profile()
        data.update(name='', headline='', contacts=[])
        original = copy.deepcopy(data)
        markdown = resume.render_text(data, markdown=True, draft=True)
        self.assertTrue(markdown.startswith('DRAFT / 草稿 - 未完成事实与交付确认\n\n## Projects'))
        self.assertEqual(data, original)


if __name__ == '__main__':
    unittest.main()
