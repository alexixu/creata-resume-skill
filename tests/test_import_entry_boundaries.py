"""Actual fictional documents check employer, action and table boundaries."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/import_resume.py'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def word_paragraph(text):
    return '<w:p><w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'


def write_document(path, paragraphs, table_after=None):
    """Write actual source files, without substituting the extraction layer."""
    if path.suffix == '.txt':
        path.write_text('\n\n'.join(paragraphs) + '\n', encoding='utf-8')
    elif path.suffix == '.html':
        body = []
        for index, text in enumerate(paragraphs):
            body.append('<p>' + escape(text) + '</p>')
            if index == table_after:
                body.append('<table><tr><td>Skills</td></tr></table>')
        path.write_text('<!doctype html><html><body>' + ''.join(body) + '</body></html>', encoding='utf-8')
    else:
        body = []
        for index, text in enumerate(paragraphs):
            body.append(word_paragraph(text))
            if index == table_after:
                body.append('<w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="3000"/></w:tblGrid>'
                            '<w:tr><w:tc><w:tcPr><w:tcW w:w="3000" w:type="dxa"/></w:tcPr>'
                            + word_paragraph('Skills') + '</w:tc></w:tr></w:tbl>')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as package:
            package.writestr('[Content_Types].xml',
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '</Types>')
            package.writestr('_rels/.rels',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/></Relationships>')
            package.writestr('word/document.xml',
                f'<w:document xmlns:w="{W}"><w:body>' + ''.join(body) + '<w:sectPr/></w:body></w:document>')


class ImportEntryBoundaryTests(unittest.TestCase):
    def import_case(self, paragraphs, suffix, table_after=None):
        temporary = tempfile.TemporaryDirectory(prefix='resume-entry-boundary-')
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        source, output = base / ('fictional' + suffix), base / 'imported'
        write_document(source, paragraphs, table_after)
        original = source.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPT), str(source), '--out', str(output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(source.read_bytes(), original)
        data = json.loads((output / 'resume.json').read_text())
        evidence = json.loads((output / 'evidence.json').read_text())
        source_data = json.loads((output / 'source.json').read_text())['sources'][0]
        review = json.loads((output / 'review.json').read_text())
        self.assertFalse(data['facts_confirmed'])
        digest = hashlib.sha256(original).hexdigest()
        self.assertEqual(source_data['sha256'], digest)
        segments = {(block['id'], index + 1)
                    for block in source_data['extraction']['blocks']
                    for index, line in enumerate(block['text'].splitlines()) if line.strip()}
        accounted = [(fact['source']['block_id'], fact['source']['locator']['line_in_block'])
                     for fact in evidence['facts']]
        self.assertEqual(len(accounted), len(segments))
        self.assertEqual(set(accounted), segments)
        self.assertTrue(all(fact['status'] == 'source_only' and fact['source']['sha256'] == digest
                            for fact in evidence['facts']))
        return data, evidence, review

    def test_chinese_dated_result_stays_with_its_employer(self):
        action = '在2022年1月-2023年12月期间整理门店报表，覆盖3家门店。'
        handoff = '编写字段说明并维护交接模板。'
        paragraphs = ['张样例', '工作经历', '虚构公司 / 运营助理 | 2021.01-2024.12', action, handoff]
        for suffix in ('.txt', '.html', '.docx'):
            with self.subTest(format=suffix):
                data, evidence, review = self.import_case(paragraphs, suffix)
                entries = data['sections'][0]['entries']
                self.assertEqual(len(entries), 1)
                self.assertEqual((entries[0]['heading'], entries[0]['date']),
                                 ('虚构公司 / 运营助理', '2021.01-2024.12'))
                self.assertEqual(entries[0]['bullets'], [action, handoff])
                fact = next(fact for fact in evidence['facts'] if fact['text'] == action)
                self.assertEqual(fact['resume_paths'], ['sections[0].entries[0].bullets[0]'])
                self.assertEqual(review['stories'][0]['heading'], '虚构公司 / 运营助理')
                self.assertIn(action, review['stories'][0]['source_quotes'])
                self.assertIn('result_scope', [item['kind'] for item in review['improvements']])

    def test_previous_employer_action_is_not_part_of_next_employer_heading(self):
        cases = [
            (['Sample Candidate', 'Experience', 'First Co / Engineer | 2021-2024',
              'Built a release checklist.', 'Next Co / Engineer | 2025-2026',
              'Maintained validation scripts.'], 'First Co / Engineer', 'Next Co / Engineer'),
            (['张样例', '工作经历', '虚构一公司 / 运营助理 | 2021-2024',
              '2023年完成报表校验，覆盖3家门店。', '虚构二公司 / 运营专员 | 2025-2026',
              '维护客户交接模板。'], '虚构一公司 / 运营助理', '虚构二公司 / 运营专员'),
        ]
        for paragraphs, first, second in cases:
            for suffix in ('.txt', '.html', '.docx'):
                with self.subTest(first=first, format=suffix):
                    data, evidence, review = self.import_case(paragraphs, suffix)
                    entries = data['sections'][0]['entries']
                    self.assertEqual([entry['heading'] for entry in entries], [first, second])
                    self.assertEqual([entry['date'] for entry in entries], ['2021-2024', '2025-2026'])
                    self.assertEqual([entry['bullets'] for entry in entries],
                                     [[paragraphs[3]], [paragraphs[5]]])
                    fact = next(fact for fact in evidence['facts'] if fact['text'] == paragraphs[3])
                    self.assertEqual(fact['resume_paths'], ['sections[0].entries[0].bullets[0]'])
                    self.assertEqual([story['heading'] for story in review['stories']], [first, second])
                    self.assertIn(paragraphs[3], review['stories'][0]['source_quotes'])

    def test_table_label_does_not_change_the_following_body_entry(self):
        paragraphs = ['Sample Candidate', 'Experience', 'First Co / Engineer | 2021-2024',
                      '- Built a release checklist.', '- Documented the engineering support handoff.']
        for suffix in ('.docx', '.html'):
            with self.subTest(format=suffix):
                data, evidence, review = self.import_case(paragraphs, suffix, table_after=3)
                experience = next(section for section in data['sections'] if section['id'] == 'experience')
                self.assertEqual(experience['entries'][0]['bullets'],
                                 ['Built a release checklist.', 'Documented the engineering support handoff.'])
                table_fact = next(fact for fact in evidence['facts'] if fact['text'] == 'Skills')
                self.assertIn('table', table_fact['source']['locator'])
                self.assertEqual(table_fact['interpretation'], 'unclassified')
                self.assertTrue(any(item['fact_id'] == table_fact['id'] for item in evidence['remaining_blocks']))
                self.assertIn('Documented the engineering support handoff.', review['stories'][0]['source_quotes'])

    def test_normal_complete_and_split_entry_headers_remain_supported(self):
        cases = [
            (['Experience', 'Example Co / Software Engineer | 2021-2024', '- Built forms.'],
             'Example Co / Software Engineer', '2021-2024'),
            (['Experience', 'Example Co', 'Software Engineer', '2021-2024', '- Built forms.'],
             'Example Co / Software Engineer', '2021-2024'),
            (['Experience', 'Example Co.', 'Software Engineer', '2021-2024', '- Built forms.'],
             'Example Co. / Software Engineer', '2021-2024'),
            (['Experience', '2023 Example Labs / Engineer', '2021-2024', '- Built forms.'],
             '2023 Example Labs / Engineer', '2021-2024'),
            (['Experience', '2021-2024', 'Example Co / Software Engineer', '- Built forms.'],
             'Example Co / Software Engineer', '2021-2024'),
            (['工作经历', '虚构开发公司 / 开发工程师 | 2021年1月-2024年12月', '- Built forms.'],
             '虚构开发公司 / 开发工程师', '2021年1月-2024年12月'),
        ]
        for paragraphs, heading, date in cases:
            for suffix in ('.txt', '.html', '.docx'):
                with self.subTest(header=heading, date=date, format=suffix):
                    data, _, _ = self.import_case(paragraphs, suffix)
                    entries = data['sections'][0]['entries']
                    self.assertEqual(len(entries), 1)
                    self.assertEqual((entries[0]['heading'], entries[0]['date']), (heading, date))
                    self.assertEqual(entries[0]['bullets'], ['Built forms.'])


if __name__ == '__main__':
    unittest.main()
