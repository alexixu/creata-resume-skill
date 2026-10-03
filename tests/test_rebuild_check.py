"""Existing-output QA is bound to the actual compiled sources, PDF and log."""
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
spec = importlib.util.spec_from_file_location('resume_rebuild', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)
PDF_TOOLS = all(shutil.which(name) for name in ('tectonic', 'pdfinfo', 'pdftotext'))
PROFILE = {
    'language': 'en', 'facts_confirmed': True, 'name': 'Sample Candidate', 'headline': '',
    'contacts': ['sample@example.com'], 'sections': [
        {'id': 'projects', 'title': 'Projects', 'entries': [
            {'heading': 'Fictional project', 'bullets': ['Built an example reporting tool.']},
        ]},
    ],
}


class RebuildCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name).resolve()
        for name, value in (
            ('resume.json', json.dumps(PROFILE)), ('resume.tex', 'private source'),
            ('resume.cls', 'private style'), ('shared note.tex', 'included private source'),
        ):
            (self.out / name).write_text(value)
        (self.out / 'fonts').mkdir()
        (self.out / 'fonts/private font.otf').write_bytes(b'fictional font')
        self.commands = []
        self.extracted_suffix = ''

    def run_command(self, command, cwd):
        self.commands.append(command[0])
        if command[0] == 'tectonic':
            generated = Path(command[command.index('--outdir') + 1])
            (generated / 'resume.pdf').write_bytes(b'fictional compiled PDF')
            Path(command[command.index('--makefile-rules') + 1]).write_text(
                './resume.pdf : resume.tex \\\n'
                '  ./resume.cls \\\n'
                '  ./shared note.tex \\\n'
                '  ./fonts/private font.otf\n')
            return 'compile succeeded\n'
        if command[0] == 'pdfinfo':
            if '-f' in command:
                return 'Page 1 size: 595.276 x 841.89 pts (A4)\n'
            return 'Pages: 1\nPage size: 595.276 x 841.89 pts (A4)\n'
        if command[0] == 'pdftotext':
            text = '\n'.join(value for _, value in resume.iter_visible_fields(PROFILE))
            return text + self.extracted_suffix
        self.fail(f'unexpected command {command}')

    def process(self, compile_pdf=True, draft=False):
        with patch.object(resume.shutil, 'which', return_value='/example/tool'), \
                patch.object(resume, 'run', side_effect=self.run_command):
            return resume.build_pdf(self.out, 1, compile_pdf=compile_pdf, draft=draft)

    def read_report(self):
        return json.loads((self.out / 'qa.json').read_text())

    def test_rebuild_preserves_private_sources_and_records_actual_dependencies(self):
        names = ['resume.tex', 'resume.cls', 'resume.json', 'fonts/private font.otf']
        originals = {name: (self.out / name).read_bytes() for name in names}
        report = self.process()
        self.assertEqual(report['status'], 'PASSED')
        for name, value in originals.items():
            self.assertEqual((self.out / name).read_bytes(), value)
            self.assertEqual(report['input_hashes'][name], resume.file_hash(self.out / name))
        self.assertIn('shared note.tex', report['input_hashes'])
        self.assertEqual(len(report['input_hashes']['resume.pdf']), 64)

    def test_check_does_not_compile_and_resets_manual_reviews(self):
        report = self.process()
        report.update(visual_review='PASS', reading_order_review='PASS')
        (self.out / 'qa.json').write_text(json.dumps(report))
        log = (self.out / 'build.log').read_bytes()
        state = (self.out / 'build-state.json').read_bytes()
        self.commands.clear()
        checked = self.process(compile_pdf=False)
        self.assertEqual(self.commands, ['pdfinfo', 'pdfinfo', 'pdftotext'])
        self.assertEqual(checked['visual_review'], 'NOT_RUN')
        self.assertEqual(checked['reading_order_review'], 'NOT_RUN')
        self.assertEqual((self.out / 'build.log').read_bytes(), log)
        self.assertEqual((self.out / 'build-state.json').read_bytes(), state)

    def test_changed_sources_pdf_font_and_log_are_rejected_repeatedly(self):
        for name in ('resume.tex', 'resume.cls', 'resume.json', 'shared note.tex',
                     'fonts/private font.otf', 'resume.pdf', 'build.log', 'build-dependencies.mk'):
            with self.subTest(file=name):
                self.process()
                original = (self.out / name).read_bytes()
                (self.out / name).write_bytes(original + b'\n')
                state = (self.out / 'build-state.json').read_bytes()
                self.commands.clear()
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, 'run rebuild before check'):
                        self.process(compile_pdf=False)
                    report = self.read_report()
                    self.assertEqual(report['status'], 'FAILED')
                    self.assertIn(name, report['stale_files'])
                    self.assertEqual(report['visual_review'], 'NOT_RUN')
                    self.assertEqual((self.out / 'build-state.json').read_bytes(), state)
                self.assertEqual(self.commands, [])
                (self.out / name).write_bytes(original)

    def test_legacy_output_without_build_state_requires_rebuild(self):
        (self.out / 'resume.pdf').write_bytes(b'old PDF')
        (self.out / 'build.log').write_text('old log')
        with self.assertRaisesRegex(ValueError, 'no readable build-state.json'):
            self.process(compile_pdf=False)
        self.assertEqual(self.commands, [])
        self.assertEqual((self.out / 'build.log').read_text(), 'old log')

    def test_failed_rebuild_cannot_reuse_previous_successful_provenance(self):
        self.process()
        error = subprocess.CalledProcessError(1, ['tectonic'], output='compile failed\n')
        with patch.object(resume.shutil, 'which', return_value='/example/tool'), \
                patch.object(resume, 'run', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'PDF compile failed'):
                resume.build_pdf(self.out, 1)
        with self.assertRaisesRegex(ValueError, 'build.log'):
            self.process(compile_pdf=False)

    def test_invalid_existing_json_fails_and_invalidates_manual_review(self):
        self.process()
        data = json.loads((self.out / 'resume.json').read_text())
        data['contact'] = 'unknown key'
        (self.out / 'resume.json').write_text(json.dumps(data))
        self.commands.clear()
        with self.assertRaisesRegex(ValueError, 'contact: unknown field'):
            self.process(compile_pdf=False)
        self.assertEqual(self.commands, [])
        report = self.read_report()
        self.assertEqual(report['stage'], 'source_validation')
        self.assertEqual(report['visual_review'], 'NOT_RUN')

    def test_unmarked_final_pdf_cannot_pass_as_draft_after_json_change(self):
        self.process()
        data = json.loads((self.out / 'resume.json').read_text())
        data['facts_confirmed'] = False
        (self.out / 'resume.json').write_text(json.dumps(data))
        private_source = (self.out / 'resume.tex').read_text()
        with self.assertRaisesRegex(ValueError, 'draft marker missing'):
            self.process(draft=True)
        self.assertFalse(self.read_report()['draft_marked'])
        self.assertEqual((self.out / 'resume.tex').read_text(), private_source)
        with self.assertRaisesRegex(ValueError, 'draft marker missing'):
            self.process(compile_pdf=False, draft=True)
        self.extracted_suffix = '\nDRAFT / 草稿 - 未完成事实与交付确认\n'
        self.assertTrue(self.process(draft=True)['draft_marked'])

    def test_symlink_retarget_invalidates_dependency_without_resolving_away_link(self):
        first, second = self.out / 'font-one.otf', self.out / 'font-two.otf'
        first.write_bytes(b'first font')
        second.write_bytes(b'second font')
        link = self.out / 'fonts/private font.otf'
        link.unlink()
        link.symlink_to(first)
        report = self.process()
        self.assertIn('fonts/private font.otf', report['input_hashes'])
        link.unlink()
        link.symlink_to(second)
        with self.assertRaisesRegex(ValueError, 'fonts/private font.otf'):
            self.process(compile_pdf=False)

    def test_dependency_parser_preserves_absolute_paths_containing_spaces(self):
        with tempfile.TemporaryDirectory(prefix='resume external fonts ') as tmp:
            external = Path(tmp).resolve() / 'Sample Font.otf'
            external.write_bytes(b'fictional external font')
            (self.out / 'build-dependencies.mk').write_text(
                './resume.pdf : resume.tex \\\n  ' + str(external) + '\n')
            names = resume.dependency_names(self.out)
            self.assertIn(str(external), names)
            self.assertIn('resume.tex', names)

    def test_dependency_parser_preserves_colon_paths_and_local_symlink_names(self):
        with tempfile.TemporaryDirectory(prefix='resume private : files ') as tmp:
            folder = Path(tmp).resolve()
            out = folder / 'candidate : software'
            out.mkdir()
            external = folder / 'shared : note.tex'
            external.write_text('fictional private source')
            local = out / 'linked : note.tex'
            local.symlink_to(external)
            (out / 'build-dependencies.mk').write_text(
                str(out / 'resume.pdf') + ' : resume.tex \\\n'
                '  ' + str(local) + ' \\\n'
                '  ' + str(external) + '\n')
            names = resume.dependency_names(out)
            self.assertIn('resume.tex', names)
            self.assertIn('linked : note.tex', names)
            self.assertIn(str(external), names)
            self.assertNotIn('software/resume.pdf : resume.tex', names)


@unittest.skipUnless(PDF_TOOLS, 'requires tectonic and Poppler')
class RealRebuildTests(unittest.TestCase):
    def test_colon_output_and_private_dependency_support_check_and_rebuild(self):
        with tempfile.TemporaryDirectory(prefix='resume-colon-path-') as tmp:
            folder = Path(tmp).resolve()
            source, out = folder / 'profile.json', folder / 'candidate : software'
            source.write_text(json.dumps(PROFILE))

            def cli(*args):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), *map(str, args)],
                                      capture_output=True, text=True, cwd=folder)

            generated = cli('render', source, '--out', out, '--theme', 'plain', '--pdf')
            self.assertEqual(generated.returncode, 0, generated.stderr)
            external = folder / 'shared : content.tex'
            external.write_text('Extra fictional private note.\n')
            dependency = out / 'linked : content.tex'
            dependency.symlink_to(external)
            private_source = (out / 'resume.tex').read_text().replace(
                '\\end{document}', '\\input{linked : content.tex}\n\\end{document}')
            (out / 'resume.tex').write_text(private_source)
            private_style = (out / 'resume.cls').read_text().replace('left=0.72in', 'left=0.73in')
            (out / 'resume.cls').write_text(private_style)
            rebuilt = cli('rebuild', out)
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['status'], 'PASSED')
            self.assertEqual(report['input_hashes'][dependency.name], resume.file_hash(external))
            self.assertIn('Extra fictional private note.', (out / 'resume-extracted.txt').read_text())
            report.update(visual_review='PASS', reading_order_review='PASS')
            (out / 'qa.json').write_text(json.dumps(report))
            checked = cli('check', out)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['visual_review'], 'NOT_RUN')
            self.assertEqual(report['reading_order_review'], 'NOT_RUN')
            old_pdf = resume.file_hash(out / 'resume.pdf')
            old_log = (out / 'build.log').read_bytes()
            old_state = (out / 'build-state.json').read_bytes()
            edited_dependency = external.read_text() + '% private dependency edit stays\n'
            external.write_text(edited_dependency)
            stale = cli('check', out)
            self.assertEqual(stale.returncode, 1)
            report = json.loads((out / 'qa.json').read_text())
            self.assertIn(dependency.name, report['stale_files'])
            self.assertEqual(resume.file_hash(out / 'resume.pdf'), old_pdf)
            self.assertEqual((out / 'build.log').read_bytes(), old_log)
            self.assertEqual((out / 'build-state.json').read_bytes(), old_state)
            rebuilt = cli('rebuild', out)
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
            self.assertEqual((out / 'resume.tex').read_text(), private_source)
            self.assertEqual((out / 'resume.cls').read_text(), private_style)
            self.assertEqual(external.read_text(), edited_dependency)
            self.assertTrue(dependency.is_symlink())
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['input_hashes'][dependency.name], resume.file_hash(external))
            self.assertNotEqual((out / 'build-state.json').read_bytes(), old_state)
            checked = cli('check', out)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['status'], 'PASSED')
            self.assertEqual(report['visual_review'], 'NOT_RUN')
            self.assertEqual(report['reading_order_review'], 'NOT_RUN')

    def test_private_edit_stale_check_rebuild_and_check_without_tectonic(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, out = folder / 'profile.json', folder / 'resume'
            source.write_text(json.dumps(PROFILE))

            def cli(*args, env=None):
                return subprocess.run([sys.executable, str(ROOT / 'scripts/resume.py'), *map(str, args)],
                                      capture_output=True, text=True, env=env)

            generated = cli('render', source, '--out', out)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            first = cli('rebuild', out)
            self.assertEqual(first.returncode, 0, first.stderr)
            old_pdf = resume.file_hash(out / 'resume.pdf')
            old_log = (out / 'build.log').read_bytes()
            (out / 'resume.tex').write_text((out / 'resume.tex').read_text() + '% private edited source\n')
            private_style = (out / 'resume.cls').read_text().replace('left=0.72in', 'left=0.73in')
            (out / 'resume.cls').write_text(private_style)
            private_source = (out / 'resume.tex').read_bytes()
            tools_only = folder / 'poppler-only'
            tools_only.mkdir()
            for name in ('pdfinfo', 'pdftotext'):
                (tools_only / name).symlink_to(shutil.which(name))
            environment = {**os.environ, 'PATH': str(tools_only)}
            stale = cli('check', out, env=environment)
            self.assertEqual(stale.returncode, 1)
            self.assertIn('run rebuild before check', stale.stderr)
            self.assertEqual(resume.file_hash(out / 'resume.pdf'), old_pdf)
            self.assertEqual((out / 'build.log').read_bytes(), old_log)
            second = cli('rebuild', out)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((out / 'resume.tex').read_bytes(), private_source)
            self.assertEqual((out / 'resume.cls').read_text(), private_style)
            checked = cli('check', out, env=environment)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            report = json.loads((out / 'qa.json').read_text())
            self.assertEqual(report['status'], 'PASSED')
            self.assertEqual(report['operation'], 'check')
            self.assertNotIn('compile', report['completed_stages'])
            self.assertEqual(report['visual_review'], 'NOT_RUN')


if __name__ == '__main__':
    unittest.main()
