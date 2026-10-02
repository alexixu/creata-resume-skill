import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('resume_coach', Path(__file__).resolve().parents[1] / 'scripts/resume_coach.py')
coach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coach)


class CoachingTests(unittest.TestCase):
    def profile(self):
        data = {'language': 'zh', 'name': '虚构姓名', 'contacts': [], 'facts_confirmed': False, 'sections': [
            {'id': 'experience', 'entries': [
                {'heading': '虚构公司 · 运营助理', 'date': '2022–2024', 'bullets': ['主导Excel报表模板，效率提升30%。']},
                {'heading': '虚构项目', 'date': '2024', 'bullets': ['参与整理用户反馈，协调产品需求。']}]}]}
        evidence = {'facts': [{'id': f'f{i}', 'text': e['bullets'][0], 'status': 'source_only',
                              'resume_paths': [f'sections[0].entries[{i}].bullets[0]']} for i, e in enumerate(data['sections'][0]['entries'])],
                    'remaining_blocks': []}
        return data, evidence

    def test_quotes_ids_and_source_status_survive_without_mutation(self):
        data, evidence = self.profile()
        before = copy.deepcopy((data, evidence))
        result = coach.plan(data, evidence, {})
        self.assertEqual(before, (data, evidence))
        self.assertEqual(len(result['stories']), 2)
        self.assertEqual(result['stories'][0]['fact_ids'], ['f0'])
        self.assertEqual(result['stories'][0]['source_quotes'], ['主导Excel报表模板，效率提升30%。'])
        self.assertEqual(result['stories'][0]['status'], 'source_only_draft')
        self.assertLessEqual(len(result['improvements']), 3)
        self.assertLessEqual(len(result['questions']), 3)
        self.assertLessEqual(len(result['directions']), 3)
        self.assertIn('result_scope', [i['kind'] for i in result['improvements']])
        self.assertIn('personal_action', [i['kind'] for i in result['improvements']])

    def test_negative_skills_titles_and_contacts_are_not_positive_evidence(self):
        data, evidence = self.profile()
        data['contacts'] = ['python@example.invalid']
        data['headline'] = 'Software engineer'
        data['sections'][0]['entries'] = [{'heading': 'Product Manager', 'date': '', 'bullets': ['没有SQL经验；不会Python开发。']}]
        result = coach.plan(data, {'facts': [], 'remaining_blocks': []}, {})
        self.assertEqual(result['directions'], [])

    def test_current_target_does_not_come_from_old_role(self):
        data, evidence = self.profile()
        data['role'] = 'software'
        result = coach.plan(data, evidence, {})
        self.assertIsNone(result['requested_target'])
        self.assertIn('target', [i['kind'] for i in result['improvements']])
        result = coach.plan(data, evidence, {'role': 'product', 'requested_target': '产品运营'})
        self.assertEqual(result['requested_target'], '产品运营')
        self.assertNotIn('target', [i['kind'] for i in result['improvements']])

    def test_integrity_questions_take_priority_and_sparse_content_stays_honest(self):
        data, evidence = self.profile()
        report = {'warnings': ['OCR page 1 needs review'], 'conflicts': [{'kind': 'dates'}]}
        result = coach.plan(data, evidence, report)
        self.assertEqual(result['questions'][0]['kind'], 'source_review')
        empty = coach.plan({'language': 'en', 'sections': []}, {'facts': []}, {'requested_target': 'Engineer'})
        self.assertEqual(empty['stories'], [])
        self.assertEqual(empty['directions'], [])
        self.assertEqual(len(empty['questions']), 1)
        self.assertIn('Insufficient', coach.render_review(empty, 'en'))

    def test_every_directed_claim_has_existing_source_id(self):
        data, evidence = self.profile()
        result = coach.plan(data, evidence, {})
        valid = {fact['id'] for fact in evidence['facts']}
        for item in result['directions'] + result['stories'] + result['improvements']:
            self.assertTrue(set(item['fact_ids']).issubset(valid))
        self.assertIn('f0', coach.render_review(result))

    def test_source_markup_does_not_become_remote_media_or_html(self):
        data, evidence = self.profile()
        data['sections'][0]['entries'][0]['heading'] = '<img src="https://example.invalid/tracker">'
        data['sections'][0]['entries'][0]['bullets'] = ['负责 ![remote](https://example.invalid/pixel)']
        text = coach.render_review(coach.plan(data, evidence, {}))
        self.assertNotIn('<img', text)
        self.assertNotIn('![remote]', text)

    def test_sales_reporting_and_general_pdf_notice_are_not_extra_evidence(self):
        data, evidence = self.profile()
        data['sections'][0]['entries'][0]['bullets'] = ['使用Excel整理每周销售报表。']
        result = coach.plan(data, evidence, {'warnings': ['PDF layout extraction does not establish semantic reading order; compare with rendered source pages']})
        self.assertNotIn('sales', [item['role'] for item in result['directions']])
        self.assertNotIn('source_review', [item['kind'] for item in result['questions']])


if __name__ == '__main__':
    unittest.main()
