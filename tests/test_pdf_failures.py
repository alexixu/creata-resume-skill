"""PDF failures leave actionable diagnostics without claiming unrun checks passed."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resume_pdf_failures', ROOT / 'scripts/resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)

PDFINFO = 'Pages: 1\nPage size: 595.276 x 841.89 pts (A4)\n'
PAGE_SIZES = 'Page 1 size: 595.276 x 841.89 pts (A4)\n'


class PdfFailureTests(unittest.TestCase):
    def simulated_commands(self, results):
        responses = iter(results)
        def run(command, cwd):
            value = next(responses)
            if isinstance(value, Exception):
                raise value
            if command[0] == 'tectonic':
                generated = Path(command[command.index('--outdir') + 1])
                (generated / 'resume.pdf').write_bytes(b'mock PDF')
                Path(command[command.index('--makefile-rules') + 1]).write_text('resume.pdf : resume.tex\n')
            return value
        return run

    def failure(self, out, stage):
        report = json.loads((out / 'qa.json').read_text())
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(report['stage'], stage)
        self.assertEqual(report['visual_review'], 'NOT_RUN')
        self.assertEqual(report['reading_order_review'], 'NOT_RUN')
        self.assertTrue(report['error'])
        self.assertTrue((out / 'build.log').read_text())
        return report

    def test_missing_dependency_preserves_source_and_records_unrun_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'resume.tex').write_text('editable source')
            with patch.object(resume.shutil, 'which', return_value=None), \
                    patch.object(resume, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'tectonic is required'):
                    resume.build_pdf(out, 1)
            run.assert_not_called()
            report = self.failure(out, 'dependencies')
            self.assertEqual(report['completed_stages'], [])
            self.assertNotIn('pages', report)
            self.assertEqual((out / 'resume.tex').read_text(), 'editable source')

    def test_compile_failure_keeps_original_compiler_output(self):
        error = subprocess.CalledProcessError(1, ['tectonic'], output='Undefined control sequence.\n')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch.object(resume.shutil, 'which', return_value='/example/tool'), \
                    patch.object(resume, 'run', side_effect=error):
                with self.assertRaisesRegex(ValueError, 'PDF compile failed') as caught:
                    resume.build_pdf(out, 1)
            report = self.failure(out, 'compile')
            self.assertEqual(report['completed_stages'], ['dependencies'])
            self.assertNotIn('text_present', report)
            self.assertIn(error.output, (out / 'build.log').read_text())
            self.assertIn(error.output, str(caught.exception))

    def test_later_command_failure_preserves_build_log_and_completed_results(self):
        for stage, results in (
            ('pdfinfo', ['compile succeeded\n', subprocess.CalledProcessError(
                1, ['pdfinfo'], output='Invalid PDF\n')]),
            ('text_extraction', ['compile succeeded\n', PDFINFO, PAGE_SIZES, subprocess.CalledProcessError(
                1, ['pdftotext'], output='Cannot extract PDF\n')]),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                with patch.object(resume.shutil, 'which', return_value='/example/tool'), \
                        patch.object(resume, 'run', side_effect=self.simulated_commands(results)):
                    with self.assertRaisesRegex(ValueError, f'PDF {stage} failed'):
                        resume.build_pdf(out, 1)
                report = self.failure(out, stage)
                log = (out / 'build.log').read_text()
                self.assertIn('compile succeeded', log)
                self.assertIn(results[-1].output, log)
                self.assertIn('compile', report['completed_stages'])
                self.assertNotIn('text_present', report)
                if stage == 'text_extraction':
                    self.assertEqual(report['pages'], 1)
                    self.assertTrue(report['a4'])

    def test_unrecognized_pdfinfo_output_produces_diagnostic_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch.object(resume.shutil, 'which', return_value='/example/tool'), \
                    patch.object(resume, 'run', side_effect=self.simulated_commands(['compile succeeded\n', 'unknown output'])):
                with self.assertRaisesRegex(ValueError, 'did not report a page count'):
                    resume.build_pdf(out, 1)
            report = self.failure(out, 'pdfinfo')
            self.assertNotIn('pages', report)


if __name__ == '__main__':
    unittest.main()
