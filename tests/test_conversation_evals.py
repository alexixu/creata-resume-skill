"""Record-integrity regression checks; these do not judge resume writing quality."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('conversation_checks', ROOT / 'scripts/check_conversation_evals.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def load_record(name='graduate-frontend'):
    return json.loads((ROOT / 'tests/conversation-evals' / (name + '.json')).read_text(encoding='utf-8'))


class ConversationRecordTests(unittest.TestCase):
    def assert_invalid(self, record, message):
        self.assertTrue(any(message in error for error in checks.check_record(record)),
                        checks.check_record(record))

    def test_saved_records_have_consistent_structure_and_references(self):
        paths = sorted((ROOT / 'tests/conversation-evals').glob('*.json'))
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(record=path.name):
                self.assertEqual(checks.check_record(json.loads(path.read_text(encoding='utf-8'))), [])

    def test_simulations_cannot_claim_independent_review(self):
        record = load_record()
        record['execution']['independent_review'] = True
        self.assert_invalid(record, 'simulation boundary')

    def test_source_quote_must_exist_in_that_user_turn(self):
        record = load_record()
        record['facts'][0]['source_quote'] = 'unrecorded evidence'
        self.assert_invalid(record, 'quote must occur')

    def test_earlier_reply_cannot_cite_a_future_fact(self):
        record = load_record()
        record['turns'][0]['claims'][0]['fact_ids'] = ['G12']
        self.assert_invalid(record, 'future fact')

    def test_corrections_preserve_reciprocal_history(self):
        record = load_record()
        next(fact for fact in record['facts'] if fact['id'] == 'G06')['superseded_by'] = 'wrong-id'
        self.assert_invalid(record, 'reciprocal history')

    def test_final_claim_cannot_reuse_superseded_fact(self):
        record = load_record()
        record['deliverables']['versions'][0]['claims'][-1]['fact_ids'] = ['G06']
        self.assert_invalid(record, 'superseded fact')

    def test_jd_mapping_cannot_reuse_superseded_fact(self):
        record = load_record()
        record['deliverables']['jd_mappings'][0]['fact_ids'] = ['G06']
        self.assert_invalid(record, 'superseded fact')

    def test_jd_mapping_status_and_target_relation_are_checked(self):
        for key, value, message in (('status', 'almost', 'must be supported'),
                                    ('target_id', 'jd-b', 'does not belong')):
            with self.subTest(field=key):
                record = load_record()
                record['deliverables']['jd_mappings'][0][key] = value
                self.assert_invalid(record, message)

    def test_score_cannot_borrow_another_versions_claim(self):
        record = load_record()
        record['deliverables']['assessments'][0]['dimensions'][0]['evidence_claim_ids'] = ['GQ4']
        self.assert_invalid(record, 'assessed version')

    def test_internally_consistent_scores_still_use_the_real_rubric(self):
        record = load_record()
        assessment = record['deliverables']['assessments'][0]
        assessment['dimensions'][0]['weight'] = 5
        assessment['assessed_score'] -= 20
        assessment['assessed_weight'] -= 20
        self.assert_invalid(record, 'rubric weight mismatch')
        record = load_record()
        record['deliverables']['assessments'][0]['dimensions'][0]['name'] = 'ATS prediction'
        self.assert_invalid(record, 'six rubric dimensions')

    def test_weighted_score_calculation_is_checked(self):
        record = load_record()
        record['deliverables']['assessments'][0]['assessed_score'] += 1
        self.assert_invalid(record, 'weighted score mismatch')

    def test_remaining_risks_cannot_be_erased(self):
        record = load_record()
        record['deliverables']['remaining_risks'] = []
        self.assert_invalid(record, 'preserve unknowns')

    def test_narrow_edit_cannot_force_interview_or_scoring(self):
        record = load_record('two-sentence-edit')
        record['turns'][0]['questions'] = ['What is your target role?']
        self.assert_invalid(record, 'must not force an interview')
        record = load_record('two-sentence-edit')
        record['deliverables']['assessments'] = [load_record()['deliverables']['assessments'][0]]
        self.assert_invalid(record, 'must not force scoring')

    def test_malformed_nested_input_has_a_failure_instead_of_a_crash(self):
        record = load_record()
        record['execution'] = None
        self.assert_invalid(record, 'invalid nested structure')

    def test_empty_record_directory_cannot_report_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(checks, 'ROOT', Path(directory)), \
                    mock.patch('sys.argv', ['check_conversation_evals.py']), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertTrue(checks.main())
            self.assertIn('no conversation records', output.getvalue())


if __name__ == '__main__':
    unittest.main()
