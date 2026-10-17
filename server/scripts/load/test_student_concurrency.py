import copy
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class StudentConcurrencyTests(unittest.TestCase):
    def subject(self):
        self.assertIsNotNone(importlib.util.find_spec('student_concurrency'), 'Concurrency evaluator is missing')
        import student_concurrency
        return student_concurrency

    def test_full_denominator_includes_missing_failures_and_invalid_hashes(self):
        subject = self.subject()
        plan = {'mode': 'login', 'count': 3, 'run_id': 'r1', 'plan_id': 'p1',
                'roster': [{'account_id': str(i), 'role': 'login'} for i in range(3)]}
        rows = [{'account_id': '0', 'plan_id': 'p1', 'run_id': 'r1', 'status': 'completed',
                 'fresh_context': True, 'login_ms': 100, 'login_http_ms': 50, 'ui_login': True},
                {'account_id': '1', 'plan_id': 'p1', 'run_id': 'r1', 'status': 'failed', 'error_code': 'timeout'}]
        result = subject.evaluate(plan, rows)
        self.assertEqual(result['counts'], {'planned': 3, 'completed': 1, 'failed': 1, 'missing': 1, 'invalid': 0, 'not_executed': 0})
        self.assertFalse(result['thresholds_met'])
        self.assertIsNone(result['capacity_verdict'])
        rows[0]['plan_id'] = 'other'
        self.assertEqual(subject.evaluate(plan, rows)['counts']['invalid'], 1)

    def test_failures_are_infinite_in_percentiles_and_subset_cannot_claim180(self):
        subject = self.subject()
        plan = {'mode': 'login', 'count': 20, 'run_id': 'r', 'plan_id': 'p',
                'roster': [{'account_id': str(i), 'role': 'login'} for i in range(20)]}
        rows = [{'account_id': str(i), 'plan_id': 'p', 'run_id': 'r', 'status': 'completed',
                 'fresh_context': True, 'login_ms': 1, 'login_http_ms': 1, 'ui_login': True} for i in range(19)]
        result = subject.evaluate(plan, rows)
        self.assertIsNone(result['login']['p99_ms'])
        self.assertFalse(result['acceptance_180'])
        self.assertFalse(result['thresholds_met'])

    def test_fullflow_requires_two_verified_answers_and_terminal_submission(self):
        subject = self.subject()
        plan = {'mode': 'full-flow', 'count': 1, 'run_id': 'r', 'plan_id': 'p',
                'roster': [{'account_id': 'a', 'role': 'login'}]}
        row = {'account_id': 'a', 'plan_id': 'p', 'run_id': 'r', 'status': 'completed',
               'fresh_context': True, 'ui_login': True, 'login_ms': 1, 'login_http_ms': 1,
               'terminal_ms': 100, 'submission_id': 's', 'final_status': 'completed', 'answers_verified': 1}
        self.assertEqual(subject.evaluate(plan, [row])['counts']['invalid'], 1)
        row['answers_verified'] = 2
        self.assertEqual(subject.evaluate(plan, [row])['counts']['completed'], 1)

    def test_duplicate_success_is_invalid_and_cannot_hide_a_missing_student(self):
        subject = self.subject()
        plan = {'mode': 'login', 'count': 1, 'run_id': 'r', 'plan_id': 'p', 'roster': [{'account_id': 'a', 'role': 'login'}]}
        row = {'account_id': 'a', 'plan_id': 'p', 'run_id': 'r', 'status': 'completed', 'fresh_context': True,
               'ui_login': True, 'login_ms': 1, 'login_http_ms': 1}
        self.assertEqual(subject.evaluate(plan, [row, copy.deepcopy(row)])['counts']['invalid'], 1)

    def test_failed_fullflow_has_exactly_two_planned_saves_even_with_partial_evidence(self):
        subject = self.subject()
        plan = {'mode': 'full-flow', 'count': 1, 'run_id': 'r', 'plan_id': 'p',
                'roster': [{'account_id': 'a', 'role': 'login'}]}
        row = {'account_id': 'a', 'plan_id': 'p', 'run_id': 'r', 'status': 'failed',
               'save_samples': [{'http_ms': 10}]}
        self.assertEqual(subject.evaluate(plan, [row])['save']['planned'], 2)

    def test_plan_cannot_relabel_a_used_fixture_as_a_new_repeat(self):
        subject = self.subject()
        fixture = {'isolated_demo': True, 'run_id': 'original', 'plan_sha': 'bad', 'fixture_spec': {},
                   'source_ownership': {'command': 'seed_prepared_startup_load'}, 'accounts': [
                       {'account_id': str(i), 'username': str(i), 'initial_state': 'prepared', 'taskId': 1,
                        'courseId': 2, 'session_id': i + 1, 'questions': [
                            {'assignment_id': 1, 'question_type': 'single_choice'},
                            {'assignment_id': 2, 'question_type': 'multiple_choice'}]} for i in range(180)]}
        with self.assertRaises(ValueError):
            subject.build_plan(fixture, mode='login', count=3, run_id='new-repeat', fixture_sha256='f',
                attestation_sha256='a', resources_sha256='r', source_sha256={}, settings={})

    def test_startup_mixed_roster_binds_120_starts_and60_savers(self):
        subject = self.subject()
        accounts = [{'account_id': str(i), 'username': str(i), 'initial_state': 'active' if i < 60 else 'prepared',
                     'taskId': 1, 'courseId': 2, 'session_id': i + 1, 'questions': [
                         {'assignment_id': 1, 'question_type': 'single_choice'},
                         {'assignment_id': 2, 'question_type': 'multiple_choice'}]} for i in range(180)]
        fixture = {'isolated_demo': True, 'run_id': 'r', 'source_ownership': {'command': 'seed_prepared_startup_load'},
                   'fixture_spec': {}, 'plan_sha': subject.digest({}), 'accounts': accounts}
        plan = subject.build_plan(fixture, mode='startup-mixed', count=180, run_id='r', fixture_sha256='f',
            attestation_sha256='a', resources_sha256='d', source_sha256={}, settings={})
        starts = [row for row in plan['roster'] if row['role'] == 'startup']
        self.assertEqual(len(starts), 120)
        self.assertEqual(sum(row['role'] == 'save' for row in plan['roster']), 60)
        self.assertEqual(len({row['attempt_id'] for row in starts}), 120)
        self.assertTrue(all(row['cache_state'] == 'cold' for row in starts))
        self.assertEqual(plan['operation_timeout_ms'], 60000)
        self.assertEqual(plan['maximum_release_skew_ms'], 1000)

    def test_startup_role_has_full_denominator_and5_10_second_thresholds(self):
        subject = self.subject()
        plan = {'mode': 'startup-mixed', 'count': 3, 'run_id': 'r', 'plan_id': 'p', 'operation_timeout_ms': 60000,
                'maximum_release_skew_ms': 1000,
                'roster': [{'account_id': str(i), 'role': 'startup', 'attempt_id': f'a{i}', 'task_id': 1,
                            'session_id': i + 1, 'cache_state': 'cold'} for i in range(3)]}
        rows = [{'account_id': str(i), 'plan_id': 'p', 'run_id': 'r', 'status': 'completed', 'fresh_context': True,
                 'startup_ms': 6000, 'release_skew_ms': 0, 'startup_witness': {'status': 'completed', 'transport': 'browser', 'attempt_id': f'a{i}',
                     'task_id': 1, 'cache_state': 'cold', 'operation_timeout_ms': 60000,
                     'activation_receipt_ids': [str(i + 1)], 'phase_origin_id': 'clock',
                     'timestamps_ms': {'click': 10, 'operable': 6010}}} for i in range(3)]
        result = subject.evaluate(plan, rows)
        self.assertEqual(result['startup']['planned'], 3)
        self.assertFalse(result['thresholds_met'])
        rows[1]['release_skew_ms'] = 1001
        self.assertEqual(subject.evaluate(plan, rows)['counts']['invalid'], 1)
        rows[1]['release_skew_ms'] = 0
        rows[0]['startup_witness']['timestamps_ms']['operable'] = 9
        result = subject.evaluate(plan, rows)
        self.assertEqual(result['counts']['invalid'], 1)
        self.assertEqual(result['startup']['observed'], 2)
        self.assertIsNone(subject.evaluate(plan, rows[:2])['startup']['p99_ms'])


if __name__ == '__main__':
    unittest.main()
