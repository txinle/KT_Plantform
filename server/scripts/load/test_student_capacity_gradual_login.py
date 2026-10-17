"""The current workload has one full roster, one ten-second arrival schedule."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import capacity_retest as contract
import student_capacity_protocol_v3 as subject
from test_student_capacity_protocol_v3 import fixture


class GradualLoginTests(unittest.TestCase):
    def plan(self, count=50, **kwargs):
        return subject.build_plan(fixture(), mode='full-flow', count=count, run_id='new-cell',
            fixture_sha256='b' * 64, base_url='http://127.0.0.1:18085', workload='paper-20q', **kwargs)

    def test_roster_schedule_is_frozen_before_browser_split(self):
        for count in (50, 180):
            with self.subTest(count=count):
                plan = self.plan(count, browser_account_ids=['a0', f'a{count - 1}'])
                self.assertEqual(plan['workload_profile'], contract.WORKLOAD_PROFILE)
                self.assertEqual(plan['login_window_ms'], 10000)
                offsets = [row['login_offset_ms'] for row in plan['roster']]
                self.assertEqual(offsets, [10000 * i / (count - 1) for i in range(count)])
                self.assertEqual(offsets[0], 0)
                self.assertEqual(offsets[-1], 10000)
                self.assertEqual(plan['roster'][1]['login_offset_ms'], 10000 / (count - 1))
                self.assertNotEqual(plan['measurement_version'], 'measurement-v4r1')
                self.assertNotEqual(plan['metric_schema_version'], 'student-capacity-metrics-v2')
        changed = self.plan(browser_account_ids=['a0', 'a49'])
        ordinary = self.plan()
        self.assertNotEqual(changed['plan_id'], ordinary['plan_id'])

    def test_current_profile_rejects_old_partial_workloads(self):
        for mode, count, workload in [('save-only', 50, 'paper-20q'), ('full-flow', 20, 'paper-20q'),
                                       ('full-flow', 50, 'comparison-2q')]:
            with self.subTest(mode=mode, count=count, workload=workload), self.assertRaises(ValueError):
                subject.build_plan(fixture(), mode=mode, count=count, run_id='new', fixture_sha256='x',
                    base_url='http://127.0.0.1:18085', workload=workload)

    def test_personal_login_clock_excludes_planned_delay_and_workload_starts_after_startup(self):
        plan = self.plan()
        row = plan['roster'][-1]
        actor = subject.Actor(fixture()['accounts'][49], row, plan, object())
        moment = [100.0]
        def login():
            actor.record['login_actual_monotonic_ms'] = moment[0] * 1000
            moment[0] += 2
            actor.record['login_ms'] = 2000
        actor.login = login
        actor.startup = lambda: moment.__setitem__(0, moment[0] + 3)
        actor.manifest = lambda: None
        def drive(start):
            self.assertEqual(start, 115)
            moment[0] += 600
        actor.drive_schedule = drive
        actor.enter = lambda _assignment: None
        actor.final_checkpoint = lambda: None
        actor.submit = lambda: None
        def sleep(seconds):
            moment[0] += seconds
        with patch.object(subject.time, 'monotonic', lambda: moment[0]), patch.object(subject.time, 'sleep', sleep), \
                patch.object(subject, 'checked', return_value={'session_id': actor.account['session_id'], 'status': 'completed'}):
            actor.run(100)
        self.assertEqual(actor.record['login_planned_monotonic_ms'], 110000)
        self.assertEqual(actor.record['login_actual_monotonic_ms'], 110000)
        self.assertEqual(actor.record['release_skew_ms'], 0)
        self.assertEqual(actor.record['login_ms'], 2000)
        self.assertEqual(actor.record['login_planned_to_ready_ms'], 2000)
        self.assertEqual(actor.record['login_ready_monotonic_ms'], 112000)
        self.assertEqual(actor.record['workload_start_monotonic_ms'], 115000)

    def test_anonymous_csrf_is_prepared_under_gate_before_the_login_release(self):
        plan = self.plan()
        moment, observations = [90.0], []
        class Gate:
            active = False
            def __enter__(self):
                self.active = True
            def __exit__(self, *_args):
                self.active = False
        gate = Gate()
        class Client:
            csrf = None
            def request(client, path, document=None, **kwargs):
                observations.append((path, gate.active, moment[0]))
                self.assertTrue(gate.active)
                self.assertEqual(path, '/api/v1/auth/csrf/')
                moment[0] += 4
                client.csrf = 'anonymous-token'
                return {'csrf_token': client.csrf}, 200, None
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, Client())
        with patch.object(subject.time, 'monotonic', lambda: moment[0]):
            actor.prepare(gate)
        self.assertEqual(observations, [('/api/v1/auth/csrf/', True, 90)])
        self.assertTrue(actor.record['anonymous_csrf_prepared'])
        self.assertEqual(actor.record['csrf_preparation_ms'], 4000)
        self.assertFalse(actor.record['login_attempts'])

    def test_current_login_reuses_prepared_csrf_and_records_first_post_not_bootstrap(self):
        plan = self.plan()
        moment, calls = [90.0], []
        task_id = fixture()['accounts'][0]['taskId']
        class Gate:
            def __enter__(self):
                pass
            def __exit__(self, *_args):
                pass
        class Client:
            csrf = None
            def request(client, path, document=None, **kwargs):
                calls.append((path, moment[0], kwargs.get('deadline')))
                if path.endswith('/csrf/'):
                    client.csrf = 'anonymous-token'
                    moment[0] += 4
                else:
                    moment[0] += 1
                return ({'results': [{'id': task_id}]} if path.endswith('/tasks/') else {}), 200, None
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, Client())
        with patch.object(subject.time, 'monotonic', lambda: moment[0]):
            actor.prepare(Gate())
            moment[0] = 100
            actor.record['login_planned_monotonic_ms'] = 100000
            actor.login()
        self.assertEqual([row[0] for row in calls], ['/api/v1/auth/csrf/', '/api/v1/auth/login/',
            '/api/v1/student/courses/', f'/api/v1/student/courses/{actor.account["courseId"]}/tasks/'])
        self.assertEqual(calls[1][1], 100)
        self.assertEqual(actor.record['login_actual_monotonic_ms'], 100000)
        self.assertEqual(actor.record['login_ms'], 3000)
        self.assertEqual(actor.record['login_total_ms'], 3000)
        self.assertEqual({row[2] for row in calls[1:]}, {130})
        self.assertEqual(contract.MEASUREMENT_VERSION, 'measurement-v5-gradual-login-r6')

    def test_current_login_without_explicit_preparation_fails_before_any_request(self):
        plan = self.plan()
        class Client:
            csrf = 'token-without-preparation-proof'
            def request(self, *_args, **_kwargs):
                raise AssertionError('A missing preparation must never shift released traffic')
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, Client())
        with self.assertRaisesRegex(subject.ProtocolFailure, 'anonymous_csrf_not_prepared'):
            actor.login()
        self.assertNotIn('login_actual_monotonic_ms', actor.record)

    def test_login_busy_retries_share_the_original_post_deadline(self):
        plan = self.plan()
        moment, deadlines, posts = [90.0], [], []
        task_id = fixture()['accounts'][0]['taskId']
        class Gate:
            def __enter__(self):
                pass
            def __exit__(self, *_args):
                pass
        class Client:
            csrf = None
            def request(client, path, document=None, **kwargs):
                if path.endswith('/csrf/'):
                    client.csrf = 'anonymous-token'
                    return {}, 200, None
                deadlines.append(kwargs['deadline'])
                if path.endswith('/login/'):
                    posts.append(moment[0])
                    moment[0] += .1
                    if len(posts) == 1:
                        return {'error': {'code': 'LOGIN_BUSY'}}, 429, 1
                return ({'results': [{'id': task_id}]} if path.endswith('/tasks/') else {}), 200, None
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, Client())
        def sleep(seconds):
            moment[0] += seconds
        with patch.object(subject.time, 'monotonic', lambda: moment[0]), patch.object(subject.time, 'sleep', sleep), \
                patch.object(subject.random, 'uniform', return_value=0):
            actor.prepare(Gate())
            moment[0] = 100
            actor.login()
        self.assertEqual(len(posts), 2)
        self.assertEqual(set(deadlines), {130})
        self.assertEqual(actor.record['login_actual_monotonic_ms'], 100000)
        self.assertAlmostEqual(actor.record['login_ms'], 1200)

    def test_formal_cells_are_four_ordered_full_flows_with_a_new_scope_identity(self):
        cells = contract.retest_cells('final', ['C1'], rounds=2)
        self.assertEqual([(c['count'], c['mode'], c['round']) for c in cells],
                         [(50, 'full-flow', 1), (180, 'full-flow', 1), (50, 'full-flow', 2), (180, 'full-flow', 2)])
        rows = [c | {'passed': True, 'browser_passed': True, 'generator_valid': True, 'monitor_ok': True,
                     'evidence_identity': {'workload_profile': contract.WORKLOAD_PROFILE}} for c in cells]
        self.assertTrue(contract.formal_passed(rows))
        legacy = copy.deepcopy(rows)
        for row in legacy:
            row['evidence_identity'].pop('workload_profile')
        self.assertFalse(contract.formal_passed(legacy))
        with self.assertRaises(ValueError):
            contract.retest_cells('qualification', ['C1'], modes=('save-only',))
        self.assertEqual(contract.phase_bounds('paper-20q')['login_window_seconds'], 10)

    def test_summary_keeps_personal_scheduled_login_and_full_arrival_window_separate(self):
        plan = self.plan(browser_account_ids=['a0', 'a49'])
        records = []
        for row in plan['roster']:
            planned = 100000 + row['login_offset_ms']
            records.append(row | {'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'status': 'completed',
                'release_skew_ms': 5, 'login_planned_monotonic_ms': planned,
                'login_actual_monotonic_ms': planned + 5, 'login_ready_monotonic_ms': planned + 105,
                'login_ms': 100, 'login_planned_to_ready_ms': 105})
        summary = subject.summarize(plan, records)
        self.assertEqual(summary['login']['planned'], 48)
        self.assertEqual(summary['login']['p95_ms'], 100)
        self.assertEqual(summary['login_planned_to_ready']['p95_ms'], 105)
        self.assertEqual(summary['login_release_window']['planned'], 50)
        self.assertEqual(summary['login_release_window']['observed'], 50)
        self.assertEqual(summary['login_release_window']['actual_window_ms'], 10000)
        self.assertEqual(summary['release_skew']['max_ms'], 5)
        records[1]['release_skew_ms'] = 1001
        self.assertFalse(subject.summarize(plan, records)['thresholds_met'])


if __name__ == '__main__':
    unittest.main()
