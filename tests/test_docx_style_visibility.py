"""Real fictional DOCX packages exercise effective Word hidden-text formatting.

Vanish toggles in a style, but direct run formatting sets an absolute value:
https://learn.microsoft.com/en-us/office/open-xml/word/how-to-remove-hidden-text-from-a-word-processing-document
Fixtures are generated locally; no Word fields are evaluated or uploaded.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/resume_extract.py'
IMPORT_SCRIPT = ROOT / 'scripts/import_resume.py'
spec = importlib.util.spec_from_file_location('docx_visibility_extract_tests', SCRIPT)
extractor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extractor)
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_R = 'http://schemas.openxmlformats.org/package/2006/relationships'


def run(text='', style=None, vanish=None, icon=False):
    properties = (f'<w:rStyle w:val="{style}"/>' if style else '')
    if vanish is not None:
        properties += f'<w:vanish w:val="{vanish}"/>'
    properties = f'<w:rPr>{properties}</w:rPr>' if properties else ''
    content = '<w:sym w:font="Wingdings" w:char="F02A"/>' if icon else f'<w:t xml:space="preserve">{escape(text)}</w:t>'
    return f'<w:r>{properties}{content}</w:r>'


def paragraph(content, style=None, numbering=''):
    properties = (f'<w:pStyle w:val="{style}"/>' if style else '') + numbering
    return f'<w:p>{"<w:pPr>" + properties + "</w:pPr>" if properties else ""}{content}</w:p>'


def style(style_id, kind='character', based_on=None, vanish=None, extra=''):
    parent = f'<w:basedOn w:val="{based_on}"/>' if based_on else ''
    properties = f'<w:rPr><w:vanish w:val="{vanish}"/></w:rPr>' if vanish is not None else ''
    return f'<w:style w:type="{kind}" w:styleId="{style_id}">{parent}{extra}{properties}</w:style>'


def hyperlink(relationship, content):
    return f'<w:hyperlink r:id="{relationship}">{content}</w:hyperlink>'


class DocxStyleVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='resume-docx-style-visibility-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.counter = 0

    def package(self, body, styles='', defaults='', relationships=None):
        """Write an actual OPC DOCX ZIP rather than mocking XML traversal."""
        self.counter += 1
        path = self.directory / f'fictional-{self.counter}.docx'
        parts = {
            '[Content_Types].xml': '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
              <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
            </Types>''',
            '_rels/.rels': f'<Relationships xmlns="{PACKAGE_R}"><Relationship Id="document" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>',
            'word/document.xml': f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}<w:sectPr/></w:body></w:document>',
            'word/styles.xml': f'<w:styles xmlns:w="{W}">{defaults}{styles}</w:styles>',
        }
        rels = [f'<Relationship Id="styles" Type="{R}/styles" Target="styles.xml"/>']
        rels += [f'<Relationship Id="{key}" Type="{R}/hyperlink" Target="{target}" TargetMode="External"/>'
                 for key, target in (relationships or {}).items()]
        parts['word/_rels/document.xml.rels'] = f'<Relationships xmlns="{PACKAGE_R}">{"".join(rels)}</Relationships>'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
            for name, content in parts.items():
                package.writestr(name, content)
        return path

    def extract(self, path):
        before = path.read_bytes()
        result = extractor.extract(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(result['source']['sha256'], hashlib.sha256(before).hexdigest())
        return result

    def assert_hidden_warning(self, result):
        self.assertTrue(any('hidden' in warning.lower() and 'omit' in warning.lower()
                            for warning in result['warnings']), result['warnings'])

    def assert_uncertain_warning(self, result):
        self.assertTrue(any(('visibility' in warning.lower() or 'hidden' in warning.lower())
                            and any(term in warning.lower() for term in ('missing', 'ambiguous', 'cycle', 'duplicate', 'unknown'))
                            for warning in result['warnings']), result['warnings'])

    def test_docdefaults_hidden_text_links_and_icons_are_omitted(self):
        defaults = '<w:docDefaults><w:rPrDefault><w:rPr><w:vanish/></w:rPr></w:rPrDefault></w:docDefaults>'
        body = paragraph(run('Fictional Candidate', vanish='false'))
        body += paragraph(run('Hidden default claim') + hyperlink('hidden', run('Hidden address'))
                          + hyperlink('hiddenIcon', run(icon=True)))
        path = self.package(body, defaults=defaults, relationships={
            'hidden': 'mailto:hidden@example.invalid', 'hiddenIcon': 'https://hidden.example.invalid/default'})
        result = self.extract(path)
        self.assertEqual([block['text'] for block in result['blocks']], ['Fictional Candidate'])
        self.assertTrue(all(not block['links'] for block in result['blocks']))
        self.assert_hidden_warning(result)

    def test_paragraph_and_character_basedon_chains_hide_text(self):
        for kind in ('paragraph', 'character'):
            with self.subTest(style_type=kind):
                styles = style('HiddenBase', kind, vanish='true') + style('HiddenDerived', kind, based_on='HiddenBase')
                content = run('Hidden inherited claim', style='HiddenDerived' if kind == 'character' else None)
                body = paragraph(run('Fictional Candidate')) + paragraph(content, style='HiddenDerived' if kind == 'paragraph' else None)
                result = self.extract(self.package(body, styles))
                self.assertEqual([block['text'] for block in result['blocks']], ['Fictional Candidate'])
                self.assert_hidden_warning(result)

    def test_style_true_twice_restores_visibility(self):
        styles = (style('ParagraphHidden', 'paragraph', vanish='true')
                  + style('CharacterToggle', 'character', vanish='true')
                  + style('CharacterBase', 'character', vanish='on')
                  + style('CharacterDerived', 'character', based_on='CharacterBase', vanish='1'))
        body = paragraph(run('Cross style visible', style='CharacterToggle'), style='ParagraphHidden')
        body += paragraph(run('Based-on visible', style='CharacterDerived'))
        result = self.extract(self.package(body, styles))
        self.assertEqual([block['text'] for block in result['blocks']], ['Cross style visible', 'Based-on visible'])

    def test_style_false_does_not_cancel_inherited_hidden(self):
        for false_value in ('false', '0', 'off'):
            with self.subTest(value=false_value):
                styles = (style('HiddenBase', 'character', vanish='true')
                          + style('NoToggle', 'character', based_on='HiddenBase', vanish=false_value))
                body = paragraph(run('Fictional Candidate')) + paragraph(run('Still hidden', style='NoToggle'))
                result = self.extract(self.package(body, styles))
                self.assertEqual([block['text'] for block in result['blocks']], ['Fictional Candidate'])
                self.assert_hidden_warning(result)

    def test_direct_false_restores_text_links_and_icons_after_inherited_hidden(self):
        styles = style('Hidden', 'character', vanish='true')
        body = paragraph(run('Fictional Candidate'))
        for index, false_value in enumerate(('false', '0', 'off')):
            body += paragraph(run(f'Visible {false_value} ', style='Hidden', vanish=false_value)
                              + hyperlink(f'mail{index}', run('Email', style='Hidden', vanish=false_value))
                              + hyperlink(f'icon{index}', run(style='Hidden', vanish=false_value, icon=True)))
        rels = {f'mail{index}': f'mailto:fictional{index}@example.invalid' for index in range(3)}
        rels.update({f'icon{index}': f'https://example.invalid/icon-{index}' for index in range(3)})
        result = self.extract(self.package(body, styles, relationships=rels))
        self.assertEqual([block['text'] for block in result['blocks']],
                         ['Fictional Candidate', 'Visible false Email', 'Visible 0 Email', 'Visible off Email'])
        for index, block in enumerate(result['blocks'][1:]):
            self.assertEqual([link['target'] for link in block['links']], [rels[f'mail{index}'], rels[f'icon{index}']])
            self.assertEqual([link['text'] for link in block['links']], ['Email', ''])

    def test_direct_true_hides_even_when_style_toggles_restore_visible(self):
        styles = style('Base', vanish='true') + style('Twice', based_on='Base', vanish='true')
        body = paragraph(run('Fictional Candidate'))
        body += paragraph(run('Directly hidden', style='Twice', vanish='true'))
        result = self.extract(self.package(body, styles))
        self.assertEqual([block['text'] for block in result['blocks']], ['Fictional Candidate'])
        self.assert_hidden_warning(result)

    def test_hidden_character_style_removes_labeled_and_icon_only_link_targets(self):
        styles = style('Hidden', vanish='true')
        hidden_text = run('Hidden label', style='Hidden')
        hidden_icon = run(style='Hidden', icon=True)
        body = paragraph(run('Contact: ') + hyperlink('hiddenLabel', hidden_text)
                          + hyperlink('hiddenIcon', hidden_icon)
                          + f'<w:fldSimple w:instr=\'HYPERLINK "https://hidden.example.invalid/field"\'>{hidden_icon}</w:fldSimple>')
        body += paragraph(hyperlink('visibleIcon', run(icon=True)))
        rels = {'hiddenLabel': 'mailto:hidden@example.invalid', 'hiddenIcon': 'https://hidden.example.invalid/icon',
                'visibleIcon': 'https://example.invalid/visible-icon'}
        result = self.extract(self.package(body, styles, relationships=rels))
        self.assertEqual([block['text'] for block in result['blocks']], ['Contact:', ''])
        self.assertEqual(result['blocks'][0]['links'], [])
        self.assertEqual(result['blocks'][1]['links'][0]['target'], rels['visibleIcon'])
        self.assertEqual(result['blocks'][1]['links'][0]['text'], '')
        self.assertNotIn('hidden.example.invalid', json.dumps(result['blocks']))
        self.assert_hidden_warning(result)

    def test_missing_style_or_basedon_retains_uncertain_source_and_warns(self):
        for styles, style_id in (('', 'Absent'), (style('Derived', based_on='Absent', vanish='true'), 'Derived')):
            for kind in ('paragraph', 'character'):
                with self.subTest(style_type=kind, style=style_id, based_on=bool(styles)):
                    current_styles = styles.replace('w:type="character"', f'w:type="{kind}"')
                    body = paragraph(run('Uncertain original text', style=style_id if kind == 'character' else None),
                                     style=style_id if kind == 'paragraph' else None)
                    result = self.extract(self.package(body, current_styles))
                    self.assertEqual([block['text'] for block in result['blocks']], ['Uncertain original text'])
                    self.assert_uncertain_warning(result)

    def test_cyclic_styles_retain_uncertain_source_with_direct_flags_still_absolute(self):
        for kind in ('paragraph', 'character'):
            with self.subTest(style_type=kind):
                styles = style('CycleA', kind, based_on='CycleB', vanish='true') + style('CycleB', kind, based_on='CycleA')
                def content(text, flag=None):
                    return paragraph(run(text, style='CycleA' if kind == 'character' else None, vanish=flag),
                                     style='CycleA' if kind == 'paragraph' else None)
                body = content('Uncertain original text') + content('Visible direct false', 'false') + content('Hidden direct true', 'true')
                result = self.extract(self.package(body, styles))
                self.assertEqual([block['text'] for block in result['blocks']], ['Uncertain original text', 'Visible direct false'])
                self.assert_uncertain_warning(result)

    def test_duplicate_style_ids_retain_original_text_and_warn(self):
        for second_kind in ('character', 'paragraph'):
            with self.subTest(second_type=second_kind):
                styles = style('Repeated', 'character', vanish='true') + style('Repeated', second_kind, vanish='false')
                body = paragraph(run('Duplicate style original text', style='Repeated'))
                result = self.extract(self.package(body, styles))
                self.assertEqual([block['text'] for block in result['blocks']], ['Duplicate style original text'])
                self.assert_uncertain_warning(result)

    def test_inherited_numbering_and_direct_cancellation_survive_visibility_resolution(self):
        numbering = '<w:pPr><w:numPr><w:numId w:val="2"/><w:ilvl w:val="0"/></w:numPr></w:pPr>'
        styles = (style('BaseList', 'paragraph', vanish='true', extra=numbering)
                  + style('DerivedList', 'paragraph', based_on='BaseList')
                  + style('VisibleToggle', 'character', vanish='true'))
        body = paragraph(run('Visible inherited list item', style='VisibleToggle'), style='DerivedList',
                         numbering='<w:numPr><w:ilvl w:val="3"/></w:numPr>')
        body += paragraph(run('Visible cancelled list', vanish='false'), style='DerivedList',
                          numbering='<w:numPr><w:numId w:val="0"/></w:numPr>')
        result = self.extract(self.package(body, styles))
        listed, cancelled = result['blocks']
        self.assertEqual(listed['text'], 'Visible inherited list item')
        self.assertEqual(listed['locator']['num_id'], '2')
        self.assertEqual(listed['locator']['numbering_level'], '3')
        self.assertEqual(listed['locator']['numbering_source'], 'style:BaseList')
        self.assertEqual(listed['locator']['numbering_status'], 'list')
        self.assertEqual(cancelled['locator']['numbering_status'], 'cancelled')
        self.assertFalse(cancelled['locator']['numbered_paragraph'])
        self.assertEqual(cancelled['text'], 'Visible cancelled list')

    def test_actual_import_cli_omits_hidden_claims_and_keeps_unconfirmed_provenance(self):
        styles = style('HiddenCharacter', vanish='true')
        body = paragraph(run('Fictional Candidate'))
        body += paragraph(run('fictional@example.invalid'))
        body += paragraph(run('Experience'))
        body += paragraph(run('Fictional Co / Engineer'))
        body += paragraph(run('2022 - 2024'))
        body += paragraph(run('Built fictional forms.'))
        body += paragraph(run('Hidden confidential claim', style='HiddenCharacter')
                          + hyperlink('hidden', run(style='HiddenCharacter', icon=True)))
        path = self.package(body, styles, relationships={'hidden': 'mailto:hidden@example.invalid'})
        before = path.read_bytes()
        output = self.directory / 'cli-output'
        process = subprocess.run([sys.executable, str(IMPORT_SCRIPT), str(path), '--out', str(output), '--language', 'en'],
                                 capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        source = json.loads((output / 'source.json').read_text())['sources'][0]
        evidence = json.loads((output / 'evidence.json').read_text())
        resume = json.loads((output / 'resume.json').read_text())
        self.assertEqual(path.read_bytes(), before)
        expected_hash = hashlib.sha256(before).hexdigest()
        self.assertEqual(source['sha256'], expected_hash)
        self.assertEqual(source['extraction']['source']['sha256'], expected_hash)
        self.assertNotIn('Hidden confidential claim', json.dumps(source['extraction']['blocks']))
        self.assertNotIn('hidden@example.invalid', json.dumps(source['extraction']['blocks']))
        self.assertNotIn('Hidden confidential claim', json.dumps(resume))
        self.assertFalse(resume['facts_confirmed'])
        self.assertTrue(evidence['facts'])
        self.assertTrue(all(fact['status'] == 'source_only' for fact in evidence['facts']))
        self.assertTrue(all(fact['source']['sha256'] == expected_hash for fact in evidence['facts']))
        self.assertEqual(evidence['coverage']['segments'], evidence['coverage']['accounted_segments'])


if __name__ == '__main__':
    unittest.main()
