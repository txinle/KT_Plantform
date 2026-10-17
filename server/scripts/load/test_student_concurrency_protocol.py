"""Synthetic protocol-driver checks; never contacts the DUT or starts load."""
import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))


class Clock:
    def __init__(self):
        self.value = 0
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class ProtocolDriverTests(unittest.TestCase):
    def subject(self):
        self.assertIsNotNone(importlib.util.find_spec('student_concurrency_protocol'), 'Protocol driver is missing')
        import student_concurrency_protocol
        return student_concurrency_protocol

    def fixture(self, mixed=False):
        from student_concurrency import digest
        rows = [{'account_id': f'a{i}', 'user_id': i + 1, 'username': f'private-user-{i}',
            'password': 'private-password', 'courseId': 10, 'taskId': 20, 'session_id': i + 100,
            'initial_state': 'active' if mixed and i < 60 else 'prepared', 'questions': [
                {'assignment_id': i * 2 + 1, 'attempt_id': i * 2 + 101, 'question_type': 'single_choice'},
                {'assignment_id': i * 2 + 2, 'attempt_id': i * 2 + 102, 'question_type': 'multiple_choice'}]}
            for i in range(180)]
        spec = {'run_id': 'r1', 'database': 'kt_e2e'}
        return {'isolated_demo': True, 'database': 'kt_e2e', 'run_id': 'r1', 'accounts': rows,
            'source_ownership': {'command': 'seed_prepared_startup_load'}, 'fixture_spec': spec,
            'plan_sha': digest(spec)}

    def plan(self, mode='full-flow', count=3, mixed=False):
        return self.subject().build_plan(self.fixture(mixed), mode=mode, count=count, run_id='r1',
            fixture_sha256='a' * 64, base_url='http://127.0.0.1:18082')

    def test_modes_freeze_real_fixture_denominators_and_final_modified_answers(self):
        subject = self.subject()
        for mode in subject.MODES:
            plan = self.plan(mode, mixed='mixed' in mode)
            self.assertEqual(len(plan['roster']), 3)
            self.assertNotIn('private-password', json.dumps(plan))
        first = self.plan()['roster'][0]
        self.assertEqual([row['value'] for row in first['save_schedule']], ['A', 'B', ['A', 'B']])
        self.assertEqual([row['value'] for row in first['answers']], ['B', ['A', 'B']])
        mixed = self.plan('startup-mixed', 180, mixed=True)
        self.assertEqual(sum(row['role'] == 'save' for row in mixed['roster']), 60)
        self.assertEqual(sum(row['role'] == 'startup' for row in mixed['roster']), 120)
        self.assertEqual(len(mixed['roster'][0]['save_schedule']), 15)

    def test_new_driver_freezes_scheduled_save_thresholds_and_login_budget(self):
        plan = self.plan()
        self.assertEqual(plan['version'], 'student-concurrency-protocol-v2')
        self.assertEqual(plan['thresholds']['save_scheduled_p95_ms'], 1000)
        self.assertEqual(plan['thresholds']['save_scheduled_p99_ms'], 3000)
        self.assertEqual(plan['login_timeout_ms'], 30000)

    def slow_save_actor(self, *, fail_at=None, delay=3):
        subject, clock, documents = self.subject(), Clock(), []
        plan = self.plan('startup-mixed', mixed=True)
        class Client:
            def __init__(self):
                self.calls = []
            def request(self, path, document=None, **kwargs):
                documents.append(copy.deepcopy(document))
                clock.sleep(delay)
                self.calls.append({'path': path, 'duration_ms': delay * 1000})
                if len(documents) == fail_at:
                    raise subject.ProtocolFailure('transport_error')
                event = document['capture_bundle']['events'][0]
                return {'committed': True, 'response_version': document['expected_version'] + 1,
                    'active_response_event_id': event['event_id'],
                    'acknowledgements': [{'event_id': event['event_id'],
                        'client_sequence_no': event['client_sequence_no'],
                        'server_ingest_sequence_no': event['client_sequence_no']}],
                    'checkpoint_ids': [row['checkpoint_id'] for row in document['capture_bundle']['checkpoints']]}, 200, 0
        actor = subject.Actor(self.fixture(True)['accounts'][0], plan['roster'][0], plan, Client())
        def enter(assignment):
            question = next(q for q in actor.account['questions'] if q['assignment_id'] == assignment)
            actor.dto = {'assignment': question, 'question': question, 'actions': {'save_draft_url': '/draft'}}
        actor.enter, actor.ensure_lease, actor.final_checkpoint = enter, lambda: None, lambda: None
        actor.lease = {}
        enter(1)
        return actor, clock, documents

    def test_slow_saves_keep_fifteen_fixed_plans_and_serial_version_dependencies(self):
        subject = self.subject()
        actor, clock, documents = self.slow_save_actor()
        with patch.object(subject.time, 'monotonic', clock.now), patch.object(subject.time, 'sleep', clock.sleep):
            actor.run(0)
        samples = actor.record['save_samples']
        self.assertEqual(len(samples), 15)
        self.assertIn('planned_monotonic_ms', samples[0])
        self.assertEqual([row['planned_monotonic_ms'] for row in samples], list(range(0, 30000, 2000)))
        self.assertEqual([row['action_start_monotonic_ms'] for row in samples], list(range(0, 45000, 3000)))
        self.assertEqual([row['http_send_monotonic_ms'] for row in samples], list(range(0, 45000, 3000)))
        self.assertEqual([row['ack_monotonic_ms'] for row in samples], list(range(3000, 48000, 3000)))
        self.assertEqual([row['client_backlog_ms'] for row in samples], list(range(0, 15000, 1000)))
        self.assertEqual([row['scheduled_ms'] for row in samples], list(range(3000, 18000, 1000)))
        self.assertEqual([row['http_ms'] for row in samples], [3000] * 15)
        self.assertEqual([row['expected_version'] for row in documents], [i // 2 for i in range(15)])
        self.assertEqual([row['answer'] for row in documents], [row['value'] for row in actor.planned['save_schedule']])

    def test_failed_save_retains_timeout_and_unexecuted_planned_slots(self):
        subject = self.subject()
        actor, clock, documents = self.slow_save_actor(fail_at=2)
        with patch.object(subject.time, 'monotonic', clock.now), patch.object(subject.time, 'sleep', clock.sleep):
            with self.assertRaisesRegex(subject.ProtocolFailure, 'transport_error'):
                actor.run(0)
        samples = actor.record['save_samples']
        self.assertEqual(len(samples), 15)
        self.assertEqual([row['status'] for row in samples], ['acknowledged', 'failed'] + ['not_executed'] * 13)
        self.assertEqual(samples[1]['http_ms'], 3000)
        self.assertIsNone(samples[1]['scheduled_ms'])
        self.assertIsNone(samples[2]['action_start_monotonic_ms'])
        report = subject.summarize(actor.plan, [actor.record])
        self.assertEqual(report['save_scheduled']['planned'], 15)
        self.assertEqual(report['save_scheduled']['observed'], 1)
        self.assertIsNone(report['save_scheduled']['p95_ms'])

    def test_fast_http_cannot_hide_scheduled_backlog_or_missing_acknowledgements(self):
        subject, plan = self.subject(), self.plan()
        rows = [{'account_id': p['account_id'], 'plan_id': plan['plan_id'], 'run_id': 'r1',
            'status': 'completed', 'release_skew_ms': 0, 'login_ms': 1, 'startup_ms': 1,
            'submission_ms': 1, 'completion_ms': 1,
            'save_samples': [{'duration_ms': 1, 'scheduled_ms': 4000}] * 3} for p in plan['roster']]
        report = subject.summarize(plan, rows)
        self.assertIn('save_scheduled', report)
        self.assertEqual(report['save']['p99_ms'], 1)
        self.assertEqual(report['save_scheduled']['p99_ms'], 4000)
        self.assertFalse(report['thresholds_met'])
        for row in rows:
            row['save_samples'] = [{'duration_ms': 1, 'scheduled_ms': 1}] * 3
        self.assertTrue(subject.summarize(plan, rows)['thresholds_met'])
        rows[0]['save_samples'] = rows[0]['save_samples'][:2]
        report = subject.summarize(plan, rows)
        self.assertEqual(report['save_scheduled']['planned'], 9)
        self.assertEqual(report['save_scheduled']['observed'], 8)
        self.assertIsNone(report['save_scheduled']['p95_ms'])
        self.assertFalse(report['thresholds_met'])

    def test_login_busy_retries_snapshot_credentials_with_one_deadline_and_counts(self):
        subject, clock, calls = self.subject(), Clock(), []
        replies = [({'error': {'code': 'LOGIN_BUSY'}}, 429, None),
                   ({'error': {'code': 'LOGIN_BUSY'}}, 429, 2), ({}, 200, 0),
                   ({'items': [{'id': 20}]}, 200, 0)]
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append((path, copy.deepcopy(document), kwargs.get('deadline')))
                clock.sleep(.1)
                actor.account['password'] = 'changed-during-retry'
                return replies.pop(0)
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        outcome = 'ok'
        with patch.object(subject.time, 'monotonic', clock.now), patch.object(subject.time, 'sleep', clock.sleep), patch.object(subject.random, 'uniform', lambda lo, hi: hi):
            try:
                actor.login()
            except subject.ProtocolFailure as error:
                outcome = str(error)
        self.assertEqual(outcome, 'ok')
        self.assertEqual([row[2] for row in calls], [30] * 4)
        self.assertEqual([row[1]['password'] for row in calls[:3]], ['private-password'] * 3)
        self.assertAlmostEqual(clock.value, 4.15)
        self.assertEqual(len(actor.record['login_attempts']), 3)
        self.assertEqual(actor.record['login_busy_responses'], 2)
        self.assertAlmostEqual(actor.record['login_total_ms'], 4150)
        report = subject.summarize(actor.plan, [actor.record])
        self.assertEqual(report['login_attempt_count'], 3)
        self.assertEqual(report['login_busy_response_count'], 2)
        self.assertNotIn('private-password', json.dumps(actor.record))
        self.assertNotIn('changed-during-retry', json.dumps(actor.record))

    def test_login_retry_budget_includes_wait_and_task_list_without_replaying_timeouts(self):
        subject = self.subject()
        for scenario in ('wait', 'task-list', 'request'):
            with self.subTest(scenario=scenario):
                clock, calls = Clock(), []
                class Client:
                    def request(self, path, document=None, **kwargs):
                        calls.append((path, kwargs.get('deadline')))
                        if scenario == 'wait':
                            clock.sleep(29)
                            return {'error': {'code': 'LOGIN_BUSY'}}, 429, 1
                        if scenario == 'request':
                            clock.sleep(30)
                            raise subject.ProtocolFailure('transport_error')
                        clock.sleep(29 if document else 1)
                        return ({'items': [{'id': 20}]} if document is None else {}), 200, 0
                actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
                outcome = 'ok'
                with patch.object(subject.time, 'monotonic', clock.now), patch.object(subject.time, 'sleep', clock.sleep), patch.object(subject.random, 'uniform', lambda lo, hi: hi):
                    try:
                        actor.login()
                    except subject.ProtocolFailure as error:
                        outcome = str(error)
                self.assertEqual(outcome, 'transport_error' if scenario == 'request' else 'login_timeout')
                self.assertEqual(len(calls), 2 if scenario == 'task-list' else 1)
                self.assertTrue(all(deadline == 30 for _, deadline in calls))
                self.assertEqual(clock.value, 30)
                self.assertEqual(len(actor.record['login_attempts']), 1)

    def test_login_only_known_busy_retries_and_all_other_failures_are_single_attempt(self):
        subject = self.subject()
        for status, code in ((400, 'INVALID_CREDENTIALS'), (403, 'CSRF_FAILED'),
                             (429, 'UNKNOWN_BUSY'), (500, 'LOGIN_BUSY'), (200, 'transport_error')):
            with self.subTest(status=status, code=code):
                calls = []
                class Client:
                    def request(self, path, document=None, **kwargs):
                        calls.append(path)
                        if code == 'transport_error':
                            raise subject.ProtocolFailure(code)
                        return {'error': {'code': code}}, status, 1
                actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
                with self.assertRaisesRegex(subject.ProtocolFailure, code):
                    actor.login()
                self.assertEqual(calls, ['/api/v1/auth/login/'])

    def test_http_retry_header_default_and_transport_remaining_budget(self):
        subject = self.subject()
        import io
        class Response(io.BytesIO):
            code = 429
            def __init__(self, header):
                super().__init__(b'{"error":{"code":"LOGIN_BUSY"}}')
                self.headers = {} if header is None else {'Retry-After': header}
        for header, expected in ((None, None), ('invalid', None), ('NaN', None), ('Infinity', None),
                                 ('-1', None), ('1.5', None), ('1', 1), ('0', 0)):
            with self.subTest(header=header):
                timeouts = []
                class Opener:
                    def open(self, request, **kwargs):
                        timeouts.append(kwargs['timeout'])
                        return Response(header)
                client = subject.HttpClient('http://127.0.0.1:18082')
                client.opener = Opener()
                with patch.object(subject.time, 'monotonic', lambda: 10):
                    self.assertEqual(client.request('/login', {}, deadline=14.5)[2], expected)
                    client.request('/login', {}, deadline=90)
                    with self.assertRaisesRegex(subject.ProtocolFailure, 'operation_timeout'):
                        client.request('/login', {}, deadline=10)
                self.assertEqual(timeouts, [4.5, 30])
                self.assertEqual(client.calls[0]['http_send_monotonic_ms'], 10000)

    def test_startup_retry_after_adds_jitter_without_moving_sixty_second_deadline(self):
        subject, clock, calls = self.subject(), Clock(), []
        replies = [({'error': {'code': 'START_BUSY'}}, 429, 1),
                   ({'status': 'executing'}, 202, 2), ({'session_id': 9}, 201, 0)]
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append((clock.now(), kwargs['deadline']))
                return replies.pop(0)
        subject.activate(Client(), 1, {}, clock=clock.now, sleep=clock.sleep, jitter=lambda lo, hi: hi)
        self.assertEqual(calls, [(0, 60), (2, 60), (6, 60)])

    def test_rejects_modified_fixture_run_and_nonisolated_or_domain_targets(self):
        subject = self.subject()
        for mutate in (lambda f: f.update(isolated_demo=False), lambda f: f.update(plan_sha='b' * 64),
                       lambda f: f.update(run_id='other')):
            fixture = self.fixture()
            mutate(fixture)
            with self.assertRaises(ValueError):
                subject.build_plan(fixture, mode='login', count=3, run_id='r1',
                    fixture_sha256='a' * 64, base_url='http://127.0.0.1:18082')
        with self.assertRaises(ValueError):
            subject.build_plan(self.fixture(), mode='login', count=3, run_id='r1',
                fixture_sha256='a' * 64, base_url='https://unapproved.example/')

    def test_activation_retries_keep_exact_identity_and_obey_retry_after(self):
        subject = self.subject()
        clock, calls = Clock(), []
        replies = [({'error': {'code': 'START_BUSY'}}, 429, 1),
            ({'status': 'executing'}, 202, 1), ({'session_id': 9, 'runtime': {}}, 201, 0)]
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append(copy.deepcopy(document))
                return replies.pop(0)
        material = {'client_instance_id': 'stable-client', 'navigation_id': 'stable-navigation'}
        result = subject.activate(Client(), 1, material, clock=clock.now, sleep=clock.sleep, jitter=lambda low, high: high)
        self.assertEqual(result['session_id'], 9)
        self.assertEqual(calls, [material] * 3)
        self.assertGreaterEqual(clock.value, 2)

    def test_activation_budget_is_sixty_seconds_and_permanent_rejection_is_not_retried(self):
        subject = self.subject()
        clock = Clock()
        class Client:
            def request(self, *args, **kwargs):
                return {'error': {'code': 'START_BUSY'}}, 429, 90
        with self.assertRaisesRegex(subject.ProtocolFailure, 'activation_timeout'):
            subject.activate(Client(), 1, {}, clock=clock.now, sleep=clock.sleep)
        self.assertEqual(clock.value, 60)
        class Rejected:
            def request(self, *args, **kwargs):
                return {'error': {'code': 'COURSE_ACCESS_DENIED'}}, 409, 0
        with self.assertRaisesRegex(subject.ProtocolFailure, 'COURSE_ACCESS_DENIED'):
            subject.activate(Rejected(), 1, {})

    def test_protocol_stream_has_no_fabricated_dom_render_events_and_retains_checkpoints(self):
        subject = self.subject()
        stream = subject.ProtocolCapture(1, 'test-client')
        event = stream.transitions(10, 'single_choice', None, None, ['A'])[0]
        self.assertEqual(event['app_version'], subject.VERSION)
        self.assertEqual(event['event_type'], 'response_transition')
        self.assertIn('clock_unsynchronized', event['quality_flags'])
        stream.accept_acknowledgements([{'event_id': event['event_id'], 'client_sequence_no': 1,
                                        'server_ingest_sequence_no': 3}])
        checkpoint = stream.checkpoint('ack_progress')
        self.assertEqual(checkpoint['highest_contiguous_acked_sequence_no'], 1)
        self.assertEqual(checkpoint['app_version'], subject.VERSION)
        self.assertEqual(stream.pending_events(), [])

    def test_save_uses_null_optional_confidence_native_capture_and_acknowledged_head(self):
        subject = self.subject()
        documents = []
        class Client:
            calls = []
            def request(self, path, document=None, **kwargs):
                documents.append(copy.deepcopy(document))
                event = document['capture_bundle']['events'][0]
                self.calls.append({'duration_ms': 10, 'path': path})
                return {'success': True, 'committed': True, 'response_version': 1,
                    'active_response_event_id': event['event_id'], 'inline_confidence': {'probability': None,
                        'answer_event_id': event['event_id']},
                    'acknowledgements': [{'event_id': event['event_id'], 'client_sequence_no': 1,
                        'server_ingest_sequence_no': 3}],
                    'checkpoint_ids': [row['checkpoint_id'] for row in document['capture_bundle']['checkpoints']]}, 200, 0
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        actor.dto = {'actions': {'save_draft_url': '/draft'}, 'assignment': {'attempt_id': 101, 'assignment_id': 1},
            'process': {}, 'question': {'question_type': 'single_choice'}}
        actor.lease = {'writer_lease_token': 'private-lease', 'writer_client_instance_id': actor.client_id}
        actor.lease_expires_at = '2099-01-01T00:00:00Z'
        actor.save({'assignment_id': 1, 'value': 'A'})
        self.assertEqual(documents[0]['inline_confidence']['probability'], None)
        self.assertEqual(documents[0]['capture_bundle']['events'][0]['payload']['after_state']['value'], 'A')
        self.assertEqual(actor.expected[101]['response_version'], 1)
        self.assertEqual(len(actor.record['event_acknowledgements']), 1)
        self.assertNotIn('private-lease', json.dumps(actor.record))
        self.assertEqual(actor.record['save_samples'][0]['duration_ms'], 10)
        self.assertEqual(actor.record['save_samples'][0]['http_ms'], 10)
        self.assertGreaterEqual(actor.record['save_samples'][0]['action_ms'], 0)

    def test_missing_failed_and_duplicate_records_stay_in_infinite_latency_denominators(self):
        subject = self.subject()
        plan = self.plan('login', 20)
        rows = [{'account_id': row['account_id'], 'plan_id': plan['plan_id'], 'run_id': 'r1',
            'status': 'completed', 'login_ms': 1, 'release_skew_ms': 0} for row in plan['roster'][:19]]
        report = subject.summarize(plan, rows)
        self.assertEqual(report['login']['planned'], 20)
        self.assertEqual(report['login']['p95_ms'], 1)
        self.assertIsNone(report['login']['p99_ms'])
        self.assertFalse(report['thresholds_met'])
        self.assertIsNone(report['capacity_verdict'])
        self.assertFalse(report['browser_operability_measured'])
        rows.append(copy.deepcopy(rows[0]))
        self.assertEqual(subject.summarize(plan, rows)['counts']['invalid'], 1)

    def test_late_submission_failure_keeps_observed_earlier_stages_and_release(self):
        subject, plan = self.subject(), self.plan('full-flow')
        rows = [{'account_id': planned['account_id'], 'plan_id': plan['plan_id'], 'run_id': 'r1',
            'status': 'completed' if index == 0 else 'failed', 'login_ms': 10 + index,
            'startup_ms': 20 + index, 'release_skew_ms': 1 + index,
            'save_samples': [{'duration_ms': 7 + index}] * (1 if index == 2 else 3)}
            for index, planned in enumerate(plan['roster'])]
        rows[0].update(submission_ms=5, completion_ms=100)
        rows[1].update(submission_ms=6, error_code='submission_timeout')
        rows[2]['error_code'] = 'transport_error'
        report = subject.summarize(plan, rows)
        self.assertEqual(report['counts']['completed'], 1)
        self.assertEqual(report['counts']['failed'], 2)
        self.assertEqual(report['login']['observed'], 3)
        self.assertEqual(report['login']['p99_ms'], 12)
        self.assertEqual(report['startup']['observed'], 3)
        self.assertEqual(report['startup']['p99_ms'], 22)
        self.assertEqual(report['save']['planned'], 9)
        self.assertEqual(report['save']['observed'], 7)
        self.assertIsNone(report['save']['p95_ms'])
        self.assertEqual(report['submission']['observed'], 2)
        self.assertEqual(report['completion']['observed'], 1)
        self.assertEqual(report['release_skew']['observed'], 3)
        self.assertEqual(report['release_skew']['max_ms'], 3)
        self.assertFalse(report['thresholds_met'])
        rows[1]['completion_ms'] = 101
        rows[2].update(submission_ms=7, completion_ms=102,
                       save_samples=[{'duration_ms': 9}] * 3)
        self.assertFalse(subject.summarize(plan, rows)['thresholds_met'])

    def test_login_timeouts_keep_all_release_measurements_but_missing_login_is_infinite(self):
        subject, plan = self.subject(), self.plan('login', 180)
        rows = [{'account_id': planned['account_id'], 'plan_id': plan['plan_id'], 'run_id': 'r1',
            'status': 'completed' if index < 131 else 'failed', 'release_skew_ms': index}
            for index, planned in enumerate(plan['roster'])]
        for row in rows[:131]:
            row['login_ms'] = 100
        report = subject.summarize(plan, rows)
        self.assertEqual(report['login']['planned'], 180)
        self.assertEqual(report['login']['observed'], 131)
        self.assertIsNone(report['login']['p95_ms'])
        self.assertEqual(report['release_skew']['observed'], 180)
        self.assertEqual(report['release_skew']['max_ms'], 179)
        self.assertFalse(report['thresholds_met'])

    def test_wrong_identity_unknown_status_and_duplicates_do_not_supply_stage_measurements(self):
        subject, plan = self.subject(), self.plan('full-flow')
        rows = [{'account_id': planned['account_id'], 'plan_id': plan['plan_id'], 'run_id': 'r1',
            'status': 'failed', 'release_skew_ms': 1, 'login_ms': 2, 'startup_ms': 3,
            'save_samples': [{'duration_ms': 4}] * 3}
            for planned in plan['roster']]
        rows[0]['run_id'] = 'wrong-run'
        rows[1]['status'] = 'unrecognized'
        rows.append(copy.deepcopy(rows[2]))
        report = subject.summarize(plan, rows)
        self.assertEqual(report['counts']['invalid'], 3)
        for metric in ('release_skew', 'login', 'startup', 'save'):
            self.assertEqual(report[metric]['observed'], 0)
            self.assertIsNone(report[metric]['p95_ms'])
        self.assertEqual(report['save']['planned'], 9)

    def test_submission_uses_complete_manifest_and_fixed_token_until_completed(self):
        subject = self.subject()
        clock, documents = Clock(), []
        replies = [({'submission_id': 'job', 'status': 'queued', 'status_url': '/status'}, 202, 0),
                   ({'submission_id': 'job', 'status': 'running'}, 200, 0),
                   ({'submission_id': 'job', 'status': 'completed'}, 200, 0)]
        class Client:
            calls = []
            def request(self, path, document=None, **kwargs):
                documents.append(copy.deepcopy(document))
                self.calls.append({'duration_ms': 1})
                return replies.pop(0)
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        actor.submit(clock=clock.now, sleep=clock.sleep)
        self.assertEqual(len(documents[0]['expected_versions']), 2)
        self.assertEqual(actor.record['submission_id'], 'job')
        self.assertEqual(actor.record['final_status'], 'completed')
        self.assertLessEqual(clock.value, 300)

    def test_native_initial_lease_metadata_never_enters_strict_save_document(self):
        subject = self.subject()
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), object())
        dto = {'entry_allowed': True, 'assignment': {'attempt_id': 101}, 'process': {},
            'entry_receipt': {'session_id': 100}, 'initial_writer_lease': {
                'writer_lease_token': 'secret', 'writer_client_instance_id': actor.client_id,
                'expires_at': '2099-01-01T00:00:00Z'}}
        actor.install(dto, deadline=1)
        self.assertEqual(set(actor.lease), {'writer_lease_token', 'writer_client_instance_id'})

    def test_native_acquire_201_and_expired_local_lease_acquire_instead_of_renew(self):
        subject = self.subject()
        calls = []
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append(path)
                return {'lease_token': 'new-private-token', 'expires_at': '2099-01-01T00:00:00Z'}, 201, 0
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        actor.dto = {'assignment': {'attempt_id': 101}, 'process': {
            'writer_lease_acquire_url': '/acquire', 'writer_lease_renew_url': '/renew'}}
        actor.lease = {'writer_lease_token': 'expired', 'writer_client_instance_id': actor.client_id}
        actor.lease_expires_at = '2000-01-01T00:00:00Z'
        actor.ensure_lease()
        self.assertEqual(calls, ['/acquire'])
        self.assertNotIn('new-private-token', json.dumps(actor.record))

    def test_http_task_list_array_and_permanent_code_are_parsed_without_credentials(self):
        subject = self.subject()
        import io
        class Response(io.BytesIO):
            code = 200
            headers = {}
        class Opener:
            def open(self, request, **kwargs):
                return Response(b'[{"id":20}]')
        client = subject.HttpClient('http://127.0.0.1:18082')
        client.opener = Opener()
        body, code, _ = client.request('/tasks')
        self.assertEqual(body, {'results': [{'id': 20}]})
        self.assertEqual(code, 200)
        class Rejected:
            def request(self, *args, **kwargs):
                return {'success': False, 'code': 'submission_not_authorized'}, 409, 0
        with self.assertRaisesRegex(subject.ProtocolFailure, 'submission_not_authorized'):
            subject.checked(Rejected(), '/finish')

    def test_submission_timeout_preserves_accepted_job_and_exact_bounded_budget(self):
        subject = self.subject()
        clock = Clock()
        class Client:
            def request(self, path, document=None, **kwargs):
                return {'submission_id': 'job', 'status': 'queued', 'status_url': '/status'}, 202 if document else 200, 0
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        with self.assertRaisesRegex(subject.ProtocolFailure, 'submission_timeout'):
            actor.submit(clock=clock.now, sleep=clock.sleep)
        self.assertEqual(clock.value, 300)
        self.assertEqual(actor.record['submission_id'], 'job')

    def test_encrypted_content_hash_length_and_decryption_use_real_protocol(self):
        subject = self.subject()
        import base64
        import gzip
        import hashlib
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        key, nonce = b'k' * 32, b'n' * 12
        expected = {'question': {'snapshot_id': 123, 'question_type': 'single_choice'}}
        cipher = AESGCM(key).encrypt(nonce, gzip.compress(json.dumps(expected).encode()), None)
        raw = json.dumps({'version': 1, 'compression': 'gzip', 'nonce': base64.b64encode(nonce).decode(),
                          'ciphertext': base64.b64encode(cipher).decode()}).encode()
        class Client:
            def get_bytes(self, *args, **kwargs):
                return raw
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        descriptor = {'url': '/frozen', 'key': base64.b64encode(key).decode(), 'byte_length': len(raw),
                      'sha256': hashlib.sha256(raw).hexdigest()}
        self.assertEqual(actor.content(descriptor, 1), expected)
        descriptor['sha256'] = '0' * 64
        with self.assertRaisesRegex(subject.ProtocolFailure, 'prepared_content_digest_mismatch'):
            actor.content(descriptor, 1)

    def test_final_checkpoint_reports_observed_all_acked_without_new_dom_event(self):
        subject = self.subject()
        documents = []
        class Client:
            def request(self, path, document=None, **kwargs):
                documents.append(document)
                return {'success': True, 'checkpoint_id': document['checkpoint_id']}, 201, 0
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), Client())
        actor.dto = {'process': {'checkpoint_url': '/checkpoint'}}
        event = actor.stream.transitions(101, 'single_choice', None, None, ['A'])[0]
        actor.stream.accept_acknowledgements([{'event_id': event['event_id'], 'client_sequence_no': 1,
                                              'server_ingest_sequence_no': 9}])
        actor.final_checkpoint()
        self.assertEqual(documents[0]['pending_event_count'], 0)
        self.assertEqual(documents[0]['highest_contiguous_acked_sequence_no'], 1)
        self.assertEqual(actor.stream.sequence, 1)
        self.assertEqual(actor.record['checkpoint_acknowledgements'], [documents[0]['checkpoint_id']])

    def test_shared_release_preserves_preparation_failures_and_all_planned_rows(self):
        subject = self.subject()
        import io
        import time
        plan = self.plan('login')
        observed = []
        class FakeActor:
            def __init__(self, account, planned, plan, client):
                self.record = {**planned, 'plan_id': plan['plan_id'], 'run_id': 'r1', 'status': 'failed'}
            def prepare(self, preparation_login_gate):
                if self.record['account_id'] == 'a0':
                    raise subject.ProtocolFailure('bad_password')
                observed.append(('prepare', self.record['account_id'], time.monotonic()))
            def run(self, released_at):
                observed.append(('run', self.record['account_id'], released_at))
                self.record['status'] = 'completed'
            def finish_record(self):
                return self.record
        output = io.StringIO()
        with patch.object(subject, 'Actor', FakeActor), patch.object(subject, 'HttpClient', lambda url: None):
            rows = subject.run(plan, self.fixture(), output)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(output.getvalue().splitlines()), 3)
        self.assertEqual(sum(row['status'] == 'failed' for row in rows), 1)
        preparations = [row[2] for row in observed if row[0] == 'prepare']
        releases = [row[2] for row in observed if row[0] == 'run']
        self.assertEqual(len(set(releases)), 1)
        self.assertGreaterEqual(releases[0], max(preparations))

    def test_180_preparation_logins_share_four_slots_and_finish_before_release(self):
        subject = self.subject()
        import io
        import threading
        import time
        plan = self.plan('startup', 180)
        csrf_barrier, lock = threading.Barrier(180), threading.Lock()
        state = {'active': 0, 'peak': 0, 'logged_in': 0, 'released': 0}
        class PreparedActor(subject.Actor):
            def csrf(self):
                csrf_barrier.wait(timeout=5)
            def login(self):
                with lock:
                    state['active'] += 1
                    state['peak'] = max(state['peak'], state['active'])
                time.sleep(.01)
                with lock:
                    state['active'] -= 1
                    state['logged_in'] += 1
            def run(self, released_at):
                with lock:
                    self_test.assertEqual(state['logged_in'], 180)
                    state['released'] += 1
                self.record['status'] = 'completed'
            def finish_record(self):
                return self.record
        self_test = self
        with patch.object(subject, 'Actor', PreparedActor), patch.object(subject, 'HttpClient', lambda url: None):
            rows = subject.run(plan, self.fixture(), io.StringIO())
        self.assertEqual(len(rows), 180)
        self.assertTrue(all(row['status'] == 'completed' for row in rows))
        self.assertEqual(state['logged_in'], 180)
        self.assertEqual(state['released'], 180)
        self.assertLessEqual(state['peak'], 4)
        self.assertEqual(plan['preparation_login_concurrency'], 4)

    def test_login_and_full_flow_workload_login_remains_180_way_without_preparation_gate(self):
        subject = self.subject()
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        class ForbiddenGate:
            def __enter__(self):
                raise AssertionError('Measured login must never acquire the preparation gate')
            def __exit__(self, *args):
                pass
        for mode in ('login', 'full-flow'):
            with self.subTest(mode=mode):
                plan, fixture = self.plan(mode, 180), self.fixture()
                login_barrier = threading.Barrier(180)
                class MeasuredActor(subject.Actor):
                    def csrf(self):
                        pass
                    def login(self):
                        login_barrier.wait(timeout=5)
                    def startup(self):
                        pass
                    def manifest(self):
                        pass
                    def save(self, action):
                        pass
                    def enter(self, assignment):
                        pass
                    def final_checkpoint(self):
                        pass
                    def submit(self):
                        pass
                actors = [MeasuredActor(account, planned, plan, None)
                          for account, planned in zip(fixture['accounts'], plan['roster'])]
                for actor in actors:
                    actor.prepare(ForbiddenGate())
                with ThreadPoolExecutor(max_workers=180) as pool:
                    futures = [pool.submit(actor.run, time.monotonic()) for actor in actors]
                    for future in futures:
                        future.result(timeout=10)
                self.assertTrue(all(actor.record['status'] == 'completed' for actor in actors))

    def test_redirect_handler_replaces_default_and_server_denied_runtime_never_becomes_ready(self):
        subject = self.subject()
        import urllib.request
        client = subject.HttpClient('http://127.0.0.1:18082')
        redirectors = [h for h in client.opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertEqual([type(h) for h in redirectors], [subject.NoRedirect])
        actor = subject.Actor(self.fixture()['accounts'][0], self.plan()['roster'][0], self.plan(), client)
        with self.assertRaisesRegex(subject.ProtocolFailure, 'SESSION_CLOSED'):
            actor.install({'entry_allowed': False, 'entry_denial_code': 'SESSION_CLOSED'}, deadline=1)

    def test_capture_quality_flags_and_transition_payload_match_current_server_schemas(self):
        subject = self.subject()
        import ast
        import jsonschema
        root = Path(__file__).resolve().parents[2] / 'apps' / 'assessment'
        assignments = ast.parse((root / 'models.py').read_text(encoding='utf-8')).body
        allowed = next(ast.literal_eval(node.value.args[0]) for node in assignments
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'ALLOWED_QUALITY_FLAGS' for target in node.targets))
        stream = subject.ProtocolCapture(1, str(subject.uuid.uuid4()))
        event = stream.transitions(101, 'single_choice', None, None, ['A'])[0]
        checkpoint = stream.checkpoint('ack_progress')
        for row in (event, checkpoint):
            self.assertTrue(set(row['quality_flags']) <= allowed)
        schema = json.loads((root / 'event_schemas' / 'response_transition' / 'v1.json').read_text(encoding='utf-8'))
        jsonschema.validate(event['payload'], schema)

    def test_full_flow_auth_start_modify_second_question_return_and_complete_native_contract(self):
        subject = self.subject()
        import time
        account, plan = self.fixture()['accounts'][0], self.plan()
        state = {101: {'version': 0, 'head': None, 'answer': None},
                 102: {'version': 0, 'head': None, 'answer': []}}
        transitions, order = [], []
        def dto(assignment, navigation='entry'):
            attempt = assignment + 100
            q = account['questions'][assignment - 1]
            return {'entry_allowed': True, 'assignment': {'attempt_id': attempt, 'assignment_id': assignment},
                'question': {'snapshot_id': assignment, 'question_type': q['question_type']},
                'draft_answer': state[attempt]['answer'], 'entry_receipt': {'session_id': 100,
                    'attempt_id': attempt, 'assignment_id': assignment, 'navigation_id': navigation,
                    'visit_id': len(order), 'visit_index': len(order), 'entry_event_id': str(subject.uuid.uuid4())},
                'prepared_paper': {'question': {'url': f'q{assignment}'}, 'manifest': {'url': 'manifest'}},
                'process': {'response_version': state[attempt]['version'], 'active_response_event_id': state[attempt]['head'],
                    'writer_lease_acquire_url': f'/lease/{attempt}/acquire', 'writer_lease_release_url': f'/lease/{attempt}/release',
                    'checkpoint_url': '/checkpoint'}, 'actions': {'save_draft_url': f'/draft/{attempt}'}}
        class Client:
            def __init__(self):
                self.calls, self.csrf = [], None
            def close(self):
                pass
            def request(self, path, document=None, **kwargs):
                order.append(path)
                self.calls.append({'path': path, 'duration_ms': 7})
                if path.endswith('/csrf/'):
                    self.csrf = 'csrf-private'
                    return {'csrf_token': self.csrf}, 200, 0
                if path.endswith('/login/'):
                    self_test.assertEqual(document['password'], 'private-password')
                    return {}, 200, 0
                if path.endswith('/tasks/'):
                    return {'items': [{'id': 20}]}, 200, 0
                if path.endswith('/start/'):
                    return {'session_id': 100, 'runtime': dto(1)}, 201, 0
                if '/questions/' in path:
                    return dto(int(path.rstrip('/').split('/')[-1]), document['navigation_id']), 200, 0
                if path.endswith('/acquire'):
                    return {'lease_token': 'private-lease', 'expires_at': '2099-01-01T00:00:00Z'}, 201, 0
                if path.endswith('/release'):
                    return {'success': True}, 200, 0
                if path == '/checkpoint':
                    self_test.assertEqual(document['pending_event_count'], 0)
                    return {'success': True, 'checkpoint_id': document['checkpoint_id']}, 201, 0
                if path.startswith('/draft/'):
                    self_test.assertEqual(set(document), {'answer', 'target_event_id', 'expected_version',
                        'client_occurred_at', 'writer_lease_token', 'writer_client_instance_id',
                        'capture_operation_id', 'inline_confidence', 'capture_bundle'})
                    attempt = int(path.split('/')[-1])
                    event = document['capture_bundle']['events'][0]
                    self_test.assertEqual(event['payload']['before_state']['value'], state[attempt]['answer'])
                    self_test.assertEqual(event['payload']['parent_event_id'], state[attempt]['head'])
                    state[attempt].update(version=state[attempt]['version'] + 1,
                        head=event['event_id'], answer=document['answer'])
                    transitions.append(event)
                    return {'committed': True, 'response_version': state[attempt]['version'],
                        'active_response_event_id': event['event_id'], 'inline_confidence': {'probability': None},
                        'acknowledgements': [{'event_id': event['event_id'], 'client_sequence_no': event['client_sequence_no'],
                                              'server_ingest_sequence_no': event['client_sequence_no'] + 2}],
                        'checkpoint_ids': [cp['checkpoint_id'] for cp in document['capture_bundle']['checkpoints']]}, 200, 0
                if path.endswith('/finish/'):
                    self_test.assertEqual([row['response_version'] for row in document['expected_versions']], [2, 1])
                    return {'submission_id': 'job', 'status': 'completed'}, 202, 0
                self_test.fail(path)
        self_test = self
        actor = subject.Actor(account, plan['roster'][0], plan, Client())
        def content(descriptor, deadline):
            if descriptor['url'] == 'manifest':
                return {'session_id': 100, 'question_count': 2, 'navigation': account['questions']}
            assignment = int(descriptor['url'][1:])
            return {'question': {'snapshot_id': assignment,
                'question_type': account['questions'][assignment - 1]['question_type']}}
        actor.content = content
        actor.prepare(subject.threading.BoundedSemaphore(4))
        self.assertEqual(order, ['/api/v1/auth/csrf/'])
        actor.run(time.monotonic())
        record = actor.finish_record()
        self.assertEqual(record['status'], 'completed')
        self.assertTrue(record['return_verified'])
        self.assertEqual([e['payload']['after_state']['value'] for e in transitions], ['A', 'B', ['A', 'B']])
        self.assertEqual(record['capture_checkpoints'][-1]['highest_contiguous_acked_sequence_no'], 3)
        self.assertEqual(record['optional_tool_api_calls'], 0)
        for secret in ('private-password', 'private-lease', 'csrf-private'):
            self.assertNotIn(secret, json.dumps(record))


if __name__ == '__main__':
    unittest.main()
