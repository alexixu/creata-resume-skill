"""Shared facts, four-language/role outputs, review gates and immutable exports."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('facts', ROOT / 'scripts/facts.py')
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


def workspace():
    return json.loads((ROOT / 'assets/examples/fact-workspace.json').read_text())


class FactVersionTests(unittest.TestCase):
    def test_single_canonical_correction_updates_four_versions_and_evidence(self):
        data = workspace()
        data['facts'][0]['value'] = 2
        data['facts'][1]['value'] = 'contributor'
        versions = facts.compile_workspace(data)
        self.assertEqual(len(versions), 4)
        for version in versions:
            zh = version['resume']['language'] == 'zh'
            expected, old = ('参与', '主导') if zh else ('Contributed to', 'Led')
            text = facts.renderer.render_text(version['resume'])
            for value in (text, '\n'.join(version['summary']), version['basis'][0]['evidence']):
                self.assertIn(expected, value)
                self.assertIn('2', value)
                self.assertNotIn(old, value)
                self.assertNotIn('3 家', value)
                self.assertNotIn('3 stores', value)
            self.assertFalse(version['resume']['facts_confirmed'])
            self.assertEqual(version['basis'][0]['fact_ids'], ['responsibility', 'store_count'])
            self.assertEqual(len(version['basis'][0]['resume_paths']), 2)
        # Compilation must not mutate templates or promote their review gate.
        self.assertTrue(data['versions'][0]['resume']['facts_confirmed'])
        self.assertEqual(data['versions'][0]['resume']['sections'][0]['entries'][0]['bullets'],
                         ['由共享事实生成概览'])

    def test_sync_creates_new_revision_marks_all_old_versions_stale_and_preserves_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, output = base / 'workspace.json', base / 'output'
            data = workspace()
            source.write_text(json.dumps(data))
            first = facts.sync(source, output)
            old = output / first['current_revision']
            manual = old / 'operations-zh' / 'resume.txt'
            manual.write_text('手工修改，请保留\n')
            original_json = (old / 'operations-zh' / 'resume.json').read_bytes()
            data['facts'][0]['value'] = 2
            data['facts'][1]['value'] = 'contributor'
            data['facts'][0]['source'] = '虚构补充 F03：纠正为 2 家门店。'
            data['facts'][1]['source'] = '虚构补充 F04：个人职责为参与。'
            source.write_text(json.dumps(data))
            second = facts.sync(source, output)
            self.assertEqual(second['current_revision'], 'revision-000002')
            self.assertEqual(second['stale_revisions'], 1)
            self.assertEqual(manual.read_text(), '手工修改，请保留\n')
            self.assertEqual((old / 'operations-zh' / 'resume.json').read_bytes(), original_json)
            snapshot = json.loads((old / 'workspace.json').read_text())
            self.assertEqual(snapshot['facts'][0]['value'], 3)
            self.assertEqual(snapshot['facts'][1]['value'], 'lead')
            self.assertIn('F01', snapshot['facts'][0]['source'])
            for version in data['versions']:
                status = json.loads((old / version['id'] / 'status.json').read_text())
                self.assertEqual(status['state'], 'STALE')
                self.assertEqual(status['superseded_by'], second['current_revision'])
                new = output / second['current_revision'] / version['id']
                self.assertEqual(json.loads((new / 'status.json').read_text())['review'], 'NEEDS_REVIEW')
                text = (new / 'resume.txt').read_text()
                self.assertIn('DRAFT', text)
                self.assertIn('2', text)
                self.assertIn('参与' if version['resume']['language'] == 'zh' else 'Contributed to', text)
            manifest = json.loads((output / 'manifest.json').read_text())
            self.assertEqual([r['state'] for r in manifest['revisions']], ['STALE', 'CURRENT'])

    def test_formal_flag_requires_each_version_gate_and_each_used_fact_confirmed(self):
        data = workspace()
        versions = facts.compile_workspace(data, confirmed_after_review=True)
        self.assertTrue(all(v['resume']['facts_confirmed'] for v in versions))
        data['facts'][0]['status'] = 'pending'
        with self.assertRaisesRegex(ValueError, 'workspace.versions\[0\].*unconfirmed.*store_count'):
            facts.compile_workspace(data, confirmed_after_review=True)
        self.assertEqual(facts.compile_workspace(data)[0]['facts'][1]['status'], 'pending')
        data['facts'][0]['status'] = 'confirmed'
        data['versions'][0]['resume']['facts_confirmed'] = False
        with self.assertRaisesRegex(ValueError, 'facts not confirmed'):
            facts.compile_workspace(data, confirmed_after_review=True)

    def test_formal_summary_and_evidence_reject_unresolved_placeholders(self):
        for field in ('summary', 'basis'):
            data = workspace()
            data['versions'][0][field][0]['template'] += '【待确认：收益口径】'
            with self.subTest(field=field), self.assertRaises(ValueError) as context:
                facts.compile_workspace(data, confirmed_after_review=True)
            self.assertIn(f'workspace.versions[0].{field}[0].template', str(context.exception))
            self.assertIn('unresolved placeholder', str(context.exception))
            # Pending editorial notes are still allowed in explicitly marked drafts.
            facts.compile_workspace(data)
        data = workspace()
        data['versions'][0]['basis'][0]['requirement'] = 'Maintain the team TODO tracker'
        facts.compile_workspace(data, confirmed_after_review=True)

    def test_failed_publication_rolls_back_every_status_and_is_retryable(self):
        for failure in ('manifest', 'old-sidecar'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source, output = base / 'workspace.json', base / 'output'
                data = workspace()
                source.write_text(json.dumps(data))
                first = facts.sync(source, output)
                old = output / first['current_revision']
                manual = old / 'operations-zh' / 'resume.txt'
                manual.write_text('保留这份手工修改\n')
                preserved = {path: path.read_bytes() for path in output.rglob('*') if path.is_file()}
                data['facts'][0]['value'] = 2
                source.write_text(json.dumps(data))
                original = facts.write_json
                failed_path = (output / 'manifest.json' if failure == 'manifest' else
                               old / 'operations-en' / 'status.json').resolve()

                def fail_write(path, value):
                    if path == failed_path:
                        raise OSError('injected publication failure')
                    return original(path, value)

                with mock.patch.object(facts, 'write_json', side_effect=fail_write):
                    with self.assertRaisesRegex(OSError, 'injected publication failure'):
                        facts.sync(source, output)
                self.assertFalse((output / 'revision-000002').exists())
                self.assertFalse((output / '.sync.lock').exists())
                self.assertEqual({path: path.read_bytes() for path in output.rglob('*') if path.is_file()}, preserved)
                manifest = json.loads((output / 'manifest.json').read_text())
                self.assertEqual(manifest['current_revision'], 'revision-000001')
                for version in data['versions']:
                    self.assertEqual(json.loads((old / version['id'] / 'status.json').read_text())['state'], 'CURRENT')
                second = facts.sync(source, output)
                self.assertEqual(second['current_revision'], 'revision-000002')
                self.assertEqual(manual.read_text(), '保留这份手工修改\n')

    def test_first_publication_failure_restores_empty_root_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, output = base / 'workspace.json', base / 'output'
            source.write_text(json.dumps(workspace()))
            original = facts.write_json

            def fail_manifest(path, value):
                if path == (output / 'manifest.json').resolve():
                    raise OSError('injected first manifest failure')
                return original(path, value)

            with mock.patch.object(facts, 'write_json', side_effect=fail_manifest):
                with self.assertRaisesRegex(OSError, 'injected first manifest failure'):
                    facts.sync(source, output)
            self.assertEqual(list(output.iterdir()), [])
            self.assertEqual(facts.sync(source, output)['current_revision'], 'revision-000001')

    def test_unknown_keys_reference_and_path_show_precise_locations(self):
        changes = [
            (lambda d: d.update(factz=[]), 'workspace.factz'),
            (lambda d: d['facts'][0].update(sorce='typo'), 'workspace.facts[0].sorce'),
            (lambda d: d['versions'][0]['bindings'][0]['refs'].update(stores='not-a-fact'),
             'workspace.versions[0].bindings[0].refs.stores'),
            (lambda d: d['versions'][0]['bindings'][0].update(path='sections[0].entries[0].bullet[0]'),
             'workspace.versions[0].bindings[0].path'),
            (lambda d: d['versions'][0]['bindings'][0].update(path='sections[0].entries[0]'),
             'workspace.versions[0].bindings[0].path'),
            (lambda d: d['versions'][0]['basis'][0].update(score=100), 'workspace.versions[0].basis[0].score'),
        ]
        for change, message in changes:
            with self.subTest(message=message):
                data = workspace()
                change(data)
                with self.assertRaises(ValueError) as context:
                    facts.compile_workspace(data)
                self.assertIn(message, str(context.exception))

    def test_duplicate_ids_and_binding_conflicts_rejected(self):
        for mutate, expected in (
            (lambda d: d['facts'].append(copy.deepcopy(d['facts'][0])), 'duplicate fact'),
            (lambda d: d['versions'].append(copy.deepcopy(d['versions'][0])), 'duplicate version'),
            (lambda d: d['versions'][0]['bindings'].append(copy.deepcopy(d['versions'][0]['bindings'][0])),
             'conflicting binding'),
        ):
            data = workspace()
            mutate(data)
            with self.assertRaisesRegex(ValueError, expected):
                facts.compile_workspace(data)

    def test_templates_reject_unused_unknown_and_attribute_tokens(self):
        for template, expected in (('{stores}', 'unused reference'),
                                   ('{responsibility} {stores} {missing}', 'unknown token'),
                                   ('{responsibility.__class__} {stores}', 'only named tokens'),
                                   ('{responsibility!r} {stores}', 'conversions'),
                                   ('{responsibility} {stores:03d}', 'specifications'),
                                   ('{responsibility} {stores', 'invalid template')):
            data = workspace()
            data['versions'][0]['bindings'][0]['template'] = template
            with self.subTest(template=template), self.assertRaisesRegex(ValueError, expected):
                facts.compile_workspace(data)

    def test_missing_language_or_choice_never_reuses_another_language(self):
        data = workspace()
        del data['facts'][0]['expressions']['en']
        with self.assertRaisesRegex(ValueError, 'expressions.en: language expression is missing'):
            facts.compile_workspace(data)
        data = workspace()
        data['facts'][1]['value'] = 'unknown-role'
        with self.assertRaisesRegex(ValueError, 'no expression for value'):
            facts.compile_workspace(data)

    def test_bad_fact_types_and_statuses_rejected(self):
        for key, value in (('value', True), ('value', 1.5), ('value', ''), ('status', 'verified'),
                           ('source', ''), ('expressions', {'de': '{value}'})):
            data = workspace()
            data['facts'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                facts.compile_workspace(data)

    def test_bad_template_types_fail_as_validation_errors(self):
        for value in (None, [], {}, 12, True):
            data = workspace()
            data['versions'][0]['bindings'][0]['template'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'must be a non-empty string'):
                facts.compile_workspace(data)

    def test_unsafe_version_ids_rejected(self):
        for value in ('../secret', '/absolute', 'one/two', '..', 'x.json', '中文', ''):
            data = workspace()
            data['versions'][0]['id'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'without paths'):
                facts.compile_workspace(data)

    def test_output_cannot_target_repository_input_or_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'workspace.json'
            source.write_text(json.dumps(workspace()))
            for output in (ROOT / 'private-test', source, source.parent, Path(tmp) / '..' / 'escaped'):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    facts.sync(source, output)
            self.assertTrue(source.exists())
            self.assertFalse((ROOT / 'private-test').exists())

    def test_existing_nonempty_output_and_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'workspace.json'
            source.write_text(json.dumps(workspace()))
            output = base / 'unrelated'
            output.mkdir()
            sentinel = output / 'resume.txt'
            sentinel.write_text('manual')
            with self.assertRaisesRegex(ValueError, 'not a facts output'):
                facts.sync(source, output)
            self.assertEqual(sentinel.read_text(), 'manual')
            link = base / 'link'
            link.symlink_to(ROOT, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'outside the public skill'):
                facts.sync(source, link / 'private-test')

    def test_history_metadata_symlink_does_not_overwrite_external_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, output = base / 'workspace.json', base / 'output'
            source.write_text(json.dumps(workspace()))
            first = facts.sync(source, output)
            external = base / 'external.json'
            external.write_text('keep')
            status = output / first['current_revision'] / 'operations-zh' / 'status.json'
            status.unlink()
            status.symlink_to(external)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                facts.sync(source, output)
            self.assertEqual(external.read_text(), 'keep')
            self.assertFalse((output / 'revision-000002').exists())

    def test_cli_check_is_read_only_and_reports_pending_fact_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'workspace.json'
            data = workspace()
            data['facts'][0]['status'] = 'pending'
            source.write_text(json.dumps(data))
            before = source.read_bytes()
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/facts.py'), 'check', str(source)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(len(report['versions']), 4)
            self.assertTrue(all(v['pending'] == ['store_count'] for v in report['versions']))
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(Path(tmp).iterdir()), [source])


if __name__ == '__main__':
    unittest.main()
