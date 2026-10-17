"""Capacity protocol contract checks; no live requests."""
import copy
from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_student_concurrency_protocol as baseline_tests


class CapacityProtocolTests(unittest.TestCase):
    def subject(self):
        import student_capacity_protocol
        return student_capacity_protocol

    def fixture(self, count=180, mixed=False):
        from student_concurrency import digest
        fixture = baseline_tests.ProtocolDriverTests().fixture(mixed)
        original = fixture['accounts']
        fixture['accounts'] = []
        for index in range(count):
            account = copy.deepcopy(original[index % 180])
            account.update(account_id=f'a{index}', user_id=index + 1, username=f'private-{index}',
                           session_id=index + 1000, initial_state='active' if mixed and index < count // 3 else 'prepared')
            for offset, question in enumerate(account['questions']):
                question.update(assignment_id=index * 2 + offset + 1, attempt_id=index * 2 + offset + 10000)
            fixture['accounts'].append(account)
        fixture['student_count'] = count
        fixture['plan_sha'] = digest(fixture['fixture_spec'])
        return fixture

    def plan(self, mode='full-flow', count=3, total=180):
        return self.subject().build_plan(self.fixture(total, 'mixed' in mode), mode=mode, count=count,
            run_id='r1', fixture_sha256='a' * 64, base_url='http://127.0.0.1:18084')

    def test_preserves_old_logical_actions_but_freezes_new_transport_identity(self):
        from student_concurrency_protocol import build_plan
        fixture = self.fixture()
        old = build_plan(fixture, mode='full-flow', count=3, run_id='r1', fixture_sha256='a' * 64,
                         base_url='http://127.0.0.1:18084')
        new = self.plan()
        self.assertEqual(old['roster'], new['roster'])
        self.assertNotEqual(old['version'], new['version'])
        self.assertEqual(new['navigation_transport'], 'save-and-leave-next-entry-v1')
        self.assertNotIn('password', str(new))

    def test_all_students_save_and_headroom_have_complete_frozen_denominators(self):
        for count in (180, 240, 360):
            plan = self.plan('save-only', count, count)
            self.assertEqual(len(plan['roster']), count)
            self.assertTrue(all(row['role'] == 'save' and len(row['save_schedule']) == 15 for row in plan['roster']))
        plan = self.plan('submission-mixed', 180)
        self.assertEqual(sum(row['role'] == 'save' for row in plan['roster']), 60)
        self.assertEqual(sum(row['role'] == 'submission' for row in plan['roster']), 120)

    def test_unsupported_or_duplicate_cohort_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan('save-only', 500, 360)
        fixture = self.fixture()
        fixture['accounts'][1]['account_id'] = fixture['accounts'][0]['account_id']
        with self.assertRaises(ValueError):
            self.subject().build_plan(fixture, mode='save-only', count=180, run_id='r1', fixture_sha256='a', base_url='http://127.0.0.1:18084')

    def test_navigation_bundles_leave_and_reuses_target_entry_identity(self):
        subject, calls = self.subject(), []
        plan = self.plan()
        actor = subject.Actor(self.fixture()['accounts'][0], plan['roster'][0], plan, object())
        actor.dto = {'assignment': {'assignment_id': 1, 'attempt_id': 10000},
                     'actions': {'save_draft_url': '/draft'},
                     'process': {'save_and_leave': {'version': 1}, 'transition_entry': {'enabled': True, 'version': 1}}}
        actor.lease = {'writer_lease_token': 'lease', 'writer_client_instance_id': actor.client_id}
        actor.ensure_lease = lambda: None
        actor.install = lambda dto, **kwargs: setattr(actor, 'dto', dto)
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append((path, copy.deepcopy(document)))
                bundle = document['save_and_leave']
                return {'success': True, 'committed': True, 'navigation_token': document['navigation_token'],
                        'writer_lease_released': True, 'response_version': 0, 'active_response_event_id': None,
                        'acknowledgements': [{'event_id': row['event_id'], 'client_sequence_no': row['client_sequence_no']} for row in bundle['events']],
                        'checkpoint_ids': [row['checkpoint_id'] for row in bundle['checkpoints']],
                        'next_entry': {'committed': True, 'runtime': {'assignment': {'assignment_id': 2}}}}, 200, None
        actor.client = Client()
        actor.enter(2)
        self.assertEqual(len(calls), 1)
        body = calls[0][1]
        self.assertEqual(body['navigation_token'], body['next_entry']['navigation_id'])
        self.assertEqual(body['next_entry']['client_instance_id'], actor.client_id)
        self.assertEqual(body['save_and_leave']['events'][0]['payload'], {'leave_reason': 'navigation', 'to_assignment_id': 2})
        self.assertEqual(actor.dto['assignment']['assignment_id'], 2)
        self.assertFalse(actor.stream.pending_events())

    def test_background_completion_gate_is_independent_of_receipt_gate(self):
        subject, plan = self.subject(), self.plan('submission')
        rows = [{'account_id': p['account_id'], 'plan_id': plan['plan_id'], 'run_id': 'r1',
                 'status': 'completed', 'release_skew_ms': 0, 'submission_ms': 1,
                 'completion_ms': 61000, 'processing_ms': 61000} for p in plan['roster']]
        report = subject.summarize(plan, rows)
        self.assertFalse(report['thresholds_met'])
        self.assertEqual(report['processing']['p95_ms'], 61000)
        rows[0].pop('processing_ms')
        self.assertIsNone(subject.summarize(plan, rows)['processing']['p95_ms'])

    def test_clock_quality_is_only_valid_after_confirmed_time_sync(self):
        subject = self.subject()
        stream = subject.CapacityCapture(10, 'a')
        self.assertEqual(stream.event(1, 'document_visible', {})['quality_flags'], ['clock_unsynchronized'])
        stream.time_sync_id = 'confirmed'
        event = stream.event(1, 'item_rendered', {'presentation_state': 'interactive'}, event_version=3)
        self.assertEqual(event['quality_flags'], [])
        self.assertEqual(event['app_version'], subject.VERSION)
        self.assertEqual(stream.checkpoint('ack_progress')['quality_flags'], [])

    def test_last_render_is_flushed_before_nonterminal_final_checkpoint(self):
        subject, calls = self.subject(), []
        plan = self.plan('save-only')
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append((path, document))
                if 'capture_bundle' in document:
                    bundle = document['capture_bundle']
                    return {'acknowledgements': [{'event_id': row['event_id'], 'client_sequence_no': row['client_sequence_no']} for row in bundle['events']],
                            'checkpoint_ids': [row['checkpoint_id'] for row in bundle['checkpoints']]}, 200, None
                return {'checkpoint_id': document['checkpoint_id']}, 201, None
        actor = subject.Actor(self.fixture()['accounts'][0], plan['roster'][0], plan, Client())
        actor.dto = {'process': {'event_batch_url': '/batch', 'checkpoint_url': '/checkpoint'}}
        actor.stream.event(10000, 'item_rendered', {'presentation_state': 'interactive'}, event_version=3)
        actor.final_checkpoint()
        self.assertEqual([path for path, _ in calls], ['/batch', '/checkpoint'])
        self.assertFalse(actor.stream.pending_events())

    def test_durable_success_receipt_accepts_nullable_public_error(self):
        import io
        subject = self.subject()
        class Response(io.BytesIO):
            code = 202
            headers = {}
        class Opener:
            def open(self, request, **kwargs):
                return Response(b'{"data":{"success":true,"status":"queued","error":null}}')
        client = subject.HttpClient('http://127.0.0.1:18084')
        client.opener = Opener()
        payload, status, _ = client.request('/finish/', {})
        self.assertEqual(status, 202)
        self.assertIsNone(payload['error'])

    def test_first_protocol_entry_records_declared_telemetry_after_time_sync_once(self):
        subject = self.subject()
        plan = self.plan()
        class Client:
            def request(self, path, document=None, **kwargs):
                return {'time_sync_id': 'sync', 'sync_quality': 'valid'}, 200, None
        actor = subject.Actor(self.fixture()['accounts'][0], plan['roster'][0], plan, Client())
        dto = {'assignment': {'attempt_id': 10000}, 'process': {'navigation_id': 'navigation',
               'time_sync_create_url': '/sync/create', 'time_sync_finalize_url': '/sync/finalize'}}
        with patch.object(subject.baseline.Actor, 'install', lambda instance, runtime, **kwargs: setattr(instance, 'dto', runtime)):
            actor.install(dto, deadline=100000)
            actor.install(dto, deadline=100000)
        events = actor.stream.pending_events()
        self.assertEqual([row['event_type'] for row in events],
                         ['pageshow', 'document_visible', 'window_focused', 'item_rendered', 'item_rendered'])
        self.assertTrue(all(row['time_sync_id'] == 'sync' for row in events))

    def test_text_and_numeric_transitions_keep_input_operations_and_empty_states(self):
        subject = self.subject()
        for kind in ('numeric', 'short_answer', 'fill_blank'):
            with self.subTest(kind=kind):
                stream = subject.CapacityCapture(1, 'client')
                rows = stream.transitions(2, kind, '', None, ['4', '', '5', '4'])
                self.assertEqual([row['payload']['after_state']['value'] for row in rows], ['4', '', '5', '4'])
                events = stream.pending_events()
                self.assertEqual([row['event_type'] for row in events],
                    ['input_operation', 'response_transition', 'text_edit_observed'] * 4)
                for index, transition in enumerate(rows):
                    self.assertEqual(events[index * 3]['payload']['operation_id'], transition['payload']['operation_id'])
                    self.assertEqual(transition['payload']['parent_event_id'], rows[index - 1]['event_id'] if index else None)

    def lease_actor(self, client):
        subject = self.subject()
        plan = self.plan()
        actor = subject.Actor(self.fixture()['accounts'][0], plan['roster'][0], plan, client)
        actor.dto = {'assignment': {'assignment_id': 1, 'attempt_id': 10000},
            'actions': {'save_draft_url': '/draft'}, 'process': {'writer_lease_renew_url': '/renew',
                'writer_lease_acquire_url': '/acquire', 'save_and_leave': {'version': 1},
                'transition_entry': {'enabled': True, 'version': 1}}}
        actor.lease = {'writer_lease_token': 'old', 'writer_client_instance_id': actor.client_id}
        actor.lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()
        return actor

    def test_installed_entry_credentials_avoid_a_first_save_acquire(self):
        class Client:
            def request(self, *args, **kwargs):
                raise AssertionError('Entry credentials must serve the first save without an acquire request')
        actor = self.lease_actor(Client())
        dto = copy.deepcopy(actor.dto)
        dto['entry_receipt'] = {'session_id': actor.account['session_id']}
        dto['initial_writer_lease'] = {'writer_lease_token': 'entered',
            'writer_client_instance_id': actor.client_id,
            'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}
        actor.stream.time_sync_id = 'existing-sync'
        actor.install(dto, deadline=0)
        actor.ensure_lease()
        self.assertEqual(actor.lease['writer_lease_token'], 'entered')
        self.assertEqual(len(actor.record['lease_acquisitions']), 1)

    def test_exact_renew409_reacquires_same_client_without_replacing_evidence(self):
        calls = []
        subject = self.subject()
        class Client:
            def request(self, path, document=None, **kwargs):
                calls.append((path, copy.deepcopy(document)))
                if path == '/renew':
                    return {'error': {'code': 'writer_lease_conflict'}}, 409, None
                return {'lease_token': 'new', 'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}, 201, None
        actor = self.lease_actor(Client())
        actor.stream.event(10000, 'document_visible', {})
        evidence, expected = copy.deepcopy(actor.stream.pending_events()), copy.deepcopy(actor.expected)
        actor.ensure_lease()
        self.assertEqual(calls, [('/renew', {'client_instance_id': actor.client_id, 'lease_token': 'old'}),
            ('/acquire', {'client_instance_id': actor.client_id})])
        self.assertEqual(actor.lease['writer_lease_token'], 'new')
        self.assertEqual(actor.stream.pending_events(), evidence)
        self.assertEqual(actor.expected, expected)
        self.assertEqual(subject.VERSION, 'student-capacity-protocol-v2')

    def test_other409_and_non409_lease_errors_are_not_retried(self):
        subject = self.subject()
        for status, code in ((409, 'response_version_conflict'), (400, 'writer_lease_conflict'), (503, 'busy')):
            calls = []
            class Client:
                def request(self, path, document=None, **kwargs):
                    calls.append(path)
                    return {'error': {'code': code}}, status, None
            actor = self.lease_actor(Client())
            with self.subTest(status=status, code=code), self.assertRaisesRegex(subject.ProtocolFailure, code):
                actor.ensure_lease()
            self.assertEqual(calls, ['/renew'])

    def test_navigation_uses_cached_lease_or_server_acquisition_and_replays_original_intent(self):
        subject = self.subject()
        for expired in (False, True):
            calls = []
            class Client:
                def request(self, path, document=None, **kwargs):
                    calls.append((path, copy.deepcopy(document)))
                    if path != '/draft':
                        raise AssertionError('Browser navigation must not make a separate renew/acquire request')
                    if len(calls) == 1:
                        raise subject.ProtocolFailure('transport_error')
                    bundle = document['save_and_leave']
                    return {'committed': True, 'navigation_token': document['navigation_token'],
                        'writer_lease_released': True, 'response_version': 0, 'active_response_event_id': None,
                        'acknowledgements': [{'event_id': row['event_id'], 'client_sequence_no': row['client_sequence_no']} for row in bundle['events']],
                        'checkpoint_ids': [row['checkpoint_id'] for row in bundle['checkpoints']],
                        'next_entry': {'committed': True, 'runtime': {'assignment': {'assignment_id': 2}}}}, 200, None
            actor = self.lease_actor(Client())
            actor.lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=-1 if expired else 3)).isoformat()
            actor.install = lambda dto, **kwargs: setattr(actor, 'dto', dto)
            with self.subTest(expired=expired), self.assertRaisesRegex(subject.ProtocolFailure, 'transport_error'):
                actor.enter(2)
            frozen = copy.deepcopy(actor._navigation_intent[1])
            actor.lease_expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            actor.enter(2)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[1][1], frozen)
            self.assertEqual('writer_lease_token' in frozen, not expired)
            self.assertEqual(frozen['save_and_leave']['client_instance_id'], actor.client_id)
            released = actor.record['lease_releases'][-1]
            self.assertIsNone(released['expires_at'])
            self.assertEqual(released['confirmation_source'], 'navigation_receipt')
            self.assertEqual(released['navigation_token'], frozen['navigation_token'])
            self.assertNotIn('lease_token', released)
