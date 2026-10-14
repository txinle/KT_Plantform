import sys
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_protocol import BrowserAlignedStream, digest
from natural_workload import CONFIDENCE_ONLY
from school_natural_load import NaturalRunner


def question(assignment, material, visit=1, user=3):
    attempt = assignment + 100
    process = {'capture_version': 'capture-v2', 'response_version': 0, 'active_response_event_id': None,
        'session_id': 7, 'attempt_id': attempt, 'visit_id': visit, 'visit_index': visit,
        'entry_event_id': f'entry-{visit}', 'navigation_id': material['navigation_id'],
        'capture_bundle': {'enabled': True, 'version': 1, 'mutation_receipts': True},
        'save_and_leave': {'enabled': True, 'version': 1}, 'atomic_submission': {'enabled': True, 'version': 1},
        'close_bundle': {'enabled': True, 'version': 1}, 'transition_entry': {'enabled': True, 'version': 1},
        'writer_lease_release_url': '/release/'}
    return {'session_id': 7, 'assignment': {'assignment_id': assignment, 'attempt_id': attempt},
        'question': {'question_type': 'single_choice'}, 'initial_response_state': None,
        'process': process, 'actions': {'save_draft_url': '/save/', 'submit_url': '/submit/'},
        'research_tools': {'rules': [], 'hints': []}, 'navigation': [],
        'offline_authorization': {'user_id': user, 'session_id': 7, 'assignment_id': assignment},
        'entry_receipt': {'session_id': 7, 'assignment_id': assignment, 'attempt_id': attempt,
            **{key: process[key] for key in ('visit_id', 'visit_index', 'entry_event_id', 'navigation_id')}}}


class TransitionProtocolTests(unittest.TestCase):
    def fixture(self, outcome='success', capability=True):
        runner = NaturalRunner('http://127.0.0.1:18080', 31, 35,
            plan={'scenario': CONFIDENCE_ONLY}, start_at=0)
        state = SimpleNamespace(session_id=7, browser_page_started=time.monotonic(),
            browser_page_origin='2026-10-13T00:00:00.000+00:00', browser_sync_due=float('inf'),
            natural_confidence_decision=None, username='isolated-driver-test')
        state.opener = Mock()
        material = runner.entry_material(state)
        stream = state.browser_stream = BrowserAlignedStream(7, started=state.browser_page_started,
            origin=state.browser_page_origin)
        dto = question(11, material)
        if not capability:
            dto['process'].pop('transition_entry')
        runtime = {'dto': dto, 'stream': stream, 'head': None, 'version': 0, 'answer': None,
            'lease': {'writer_lease_token': 'lease', 'writer_client_instance_id': stream.client_instance_id}}
        state.browser_current = runtime
        runner.ledger['7'] = {'answers': {}, 'confidence': {'111': {}}, 'events': {}, 'event_ids': [], 'submission_tokens': []}
        calls, entries = [], {}
        source_acks = {}
        def entered(assignment, body):
            token = body['navigation_id']
            entries.setdefault(token, question(assignment, body, visit=len(entries) + 2))
            return deepcopy(entries[token])
        def post(_, path, body, metric):
            calls.append((metric, deepcopy(body)))
            if metric == 'enter_item':
                dto = entered(int(path.strip('/').split('/')[-1]), body)
                if outcome == 'entry_loss' and len([name for name, _ in calls if name == 'enter_item']) == 1:
                    raise RuntimeError('entry response lost after commit')
                return dto
            bundle = body.get('save_and_leave') or body.get('capture_bundle') or body.get('atomic_submission')
            receipt = {'success': True, 'committed': True,
                'acknowledgements': [{'event_id': event['event_id'], 'client_sequence_no': event['client_sequence_no'],
                    'server_ingest_sequence_no': event['client_sequence_no']}
                    for event in bundle['events']], 'checkpoint_ids': [row['checkpoint_id'] for row in bundle['checkpoints']],
                'response_version': 0, 'active_response_event_id': None, 'final_response_event_id': None,
                'writer_lease_released': True, 'status': 'submitted'}
            if metric == 'submit_item':
                return {**receipt, 'next_url': '/student/sessions/7/questions/12/'}
            if metric == 'save_and_leave':
                receipt.update(navigation_token=body['navigation_token'], to_assignment_id=body['to_assignment_id'])
            else:
                receipt['capture_operation_id'] = body['capture_operation_id']
            if 'next_entry' in body:
                entry = body['next_entry']
                if outcome in ('partial', 'entry_loss'):
                    receipt['next_entry'] = {'committed': False, 'error': {'code': 'ENTRY_OUTCOME_UNKNOWN', 'message': 'retry'}}
                elif outcome == 'malformed':
                    receipt['next_entry'] = {'committed': True}
                else:
                    target = entered(entry['assignment_id'], entry)
                    if outcome == 'wrong_account':
                        target['offline_authorization']['user_id'] = 4
                    elif outcome == 'missing_receipt':
                        target.pop('entry_receipt')
                    elif outcome == 'wrong_navigation':
                        target['entry_receipt']['navigation_id'] = 'wrong-navigation'
                    elif outcome == 'wrong_process_session':
                        target['process']['session_id'] = 8
                    elif outcome == 'malformed_process':
                        target['process'] = None
                    receipt['next_entry'] = {'committed': True, 'runtime': target}
            token = body.get('navigation_token') or body.get('capture_operation_id')
            if outcome == 'loss' and token not in source_acks:
                source_acks[token] = receipt
                raise RuntimeError('response lost after commit')
            return receipt
        runner._browser_post = Mock(side_effect=post)
        runner._browser_request = Mock(return_value={})
        runner._browser_flush = Mock()
        runner._browser_ensure_lease = Mock()
        return runner, state, runtime, calls, entries

    def test_navigation_uses_committed_target_once_and_keeps_render_capture_and_auth(self):
        runner, state, runtime, calls, entries = self.fixture()
        opened = runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
        self.assertEqual([name for name, _ in calls], ['save_and_leave'])
        entry = calls[0][1]['next_entry']
        visit = runner.ledger['7']['visits'][0]
        self.assertEqual(visit['entry_material'], {key: value for key, value in entry.items() if key != 'assignment_id'})
        self.assertEqual(visit['entry_receipt']['navigation_id'], entry['navigation_id'])
        self.assertEqual(len(entries), 1)
        self.assertEqual(opened['dto']['assignment']['assignment_id'], 12)
        self.assertEqual(runtime['lease'], {})
        runner._browser_request.assert_called_once_with(state, '/api/v1/auth/session/', 'offline_authorization')
        self.assertIn('item_rendered', [event['event_type'] for event in state.browser_stream.pending_events()])

    def test_partial_success_retries_only_entry_with_original_clock_material(self):
        runner, state, runtime, calls, entries = self.fixture('partial')
        runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
        self.assertEqual([name for name, _ in calls], ['save_and_leave', 'enter_item'])
        self.assertIn('next_entry', calls[0][1])
        entry = calls[0][1]['next_entry']
        self.assertEqual(calls[1][1], {key: value for key, value in entry.items() if key != 'assignment_id'})
        self.assertTrue(runtime['navigation_acknowledged'])
        self.assertEqual(runtime['lease'], {})
        self.assertEqual(len(entries), 1)

    def test_malformed_target_stops_after_source_ack_without_repeating_source(self):
        runner, state, runtime, calls, _ = self.fixture('malformed')
        with self.assertRaisesRegex(RuntimeError, 'target entry'):
            runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
        self.assertTrue(runtime['navigation_acknowledged'])
        self.assertEqual(runtime['lease'], {})
        with self.assertRaisesRegex(RuntimeError, 'target entry'):
            runner._browser_close(state, runtime, 'navigation', 12)
        self.assertEqual([name for name, _ in calls], ['save_and_leave'])
        row = next(iter(runner.ledger['7']['transition_entries'].values()))
        self.assertEqual(row['outcome'], 'unknown')

    def test_wrong_account_dto_is_not_reused(self):
        runner, state, runtime, calls, _ = self.fixture('wrong_account')
        with self.assertRaisesRegex(RuntimeError, 'target entry'):
            runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
        self.assertTrue(runtime['navigation_acknowledged'])
        self.assertEqual([name for name, _ in calls], ['save_and_leave'])

    def test_wrong_identity_or_missing_target_receipt_remains_unknown(self):
        for outcome in ('missing_receipt', 'wrong_navigation', 'wrong_process_session', 'malformed_process'):
            with self.subTest(outcome=outcome):
                runner, state, runtime, calls, _ = self.fixture(outcome)
                with self.assertRaisesRegex(RuntimeError, 'target entry'):
                    runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
                self.assertTrue(runtime['navigation_acknowledged'])
                self.assertEqual(runtime['lease'], {})
                self.assertEqual([name for name, _ in calls], ['save_and_leave'])

    def test_changed_frozen_entry_clock_cannot_consume_confirmed_dto(self):
        runner, state, runtime, _, _ = self.fixture()
        runner._browser_start_action(state, 'navigation_next_ready', 0)
        dto = runner._browser_close(state, runtime, 'navigation', 12)
        state.browser_entry_material['client_monotonic_ms'] += 1
        with self.assertRaisesRegex(RuntimeError, 'target entry material changed'):
            runner.browser_open_question(state, 12, 'navigation', dto)
        self.assertNotIn('visits', runner.ledger['7'])

    def test_partial_then_target_response_loss_only_replays_target_and_deduplicates_visit(self):
        runner, state, runtime, calls, entries = self.fixture('entry_loss')
        with self.assertRaisesRegex(RuntimeError, 'entry response lost'):
            runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
        runner.browser_open_question(state, 12, 'navigation')
        self.assertEqual([name for name, _ in calls], ['save_and_leave', 'enter_item', 'enter_item'])
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(runner.ledger['7']['visits']), 1)

    def test_navigation_ledger_binds_next_entry_and_preserves_old_material_fingerprint(self):
        for capability in (False, True):
            with self.subTest(capability=capability):
                runner, state, runtime, calls, _ = self.fixture(capability=capability)
                runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
                body = calls[0][1]
                receipt = runtime['navigation_receipt']
                with patch('assessment_300.Runner.request', return_value=(200, receipt, .001)):
                    runner.request(state, '/save/', method='POST', body=body)
                row = runner.ledger['7']['navigation_receipts'][body['navigation_token']]
                fields = ['save_and_leave', 'leave_event_id', 'target_event_id', 'to_assignment_id', 'client_clicked_at']
                if capability:
                    fields += ['next_entry']
                self.assertEqual(row['material_fingerprint'], digest({key: body.get(key) for key in fields}))
                self.assertEqual(row['receipt_sha256'], digest({key: value for key, value in receipt.items() if key != 'next_entry'}))
                if capability:
                    altered = deepcopy(body)
                    altered['next_entry']['client_monotonic_ms'] += 1
                    with self.assertRaisesRegex(RuntimeError, 'Navigation material changed'):
                        runner.request(state, '/save/', method='POST', body=altered)

    def test_navigation_response_loss_replays_identical_source_and_target_and_deduplicates_visit(self):
        runner, state, runtime, calls, entries = self.fixture('loss')
        runner._browser_start_action(state, 'navigation_next_ready', 0)
        with self.assertRaisesRegex(RuntimeError, 'response lost'):
            runner._browser_close(state, runtime, 'navigation', 12)
        dto = runner._browser_close(state, runtime, 'navigation', 12)
        runner.browser_open_question(state, 12, 'navigation', dto)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(runner.ledger['7']['visits']), 1)

    def test_old_capability_uses_full_dedicated_entry_protocol(self):
        runner, state, runtime, calls, _ = self.fixture(capability=False)
        runner.navigate(state, runtime, 12, 0, 'navigation_next_ready')
        self.assertEqual([name for name, _ in calls], ['save_and_leave', 'enter_item'])
        self.assertNotIn('next_entry', calls[0][1])

    def test_submit_close_reuses_target_after_atomic_submission(self):
        runner, state, runtime, calls, entries = self.fixture()
        opened = runner._submit_material(state, runtime, 0,
            {'payload': {'after_state_hash': 'hash'}}, list(range(11, 31)))
        self.assertEqual([name for name, _ in calls], ['submit_item', 'close_bundle'])
        self.assertEqual(opened['dto']['assignment']['assignment_id'], 12)
        self.assertEqual(len(entries), 1)
        self.assertTrue(runtime['close_acknowledged'])

    def test_submit_target_clock_is_frozen_before_source_leave_event(self):
        runner, state, runtime, _, _ = self.fixture()
        event = runtime['stream'].event
        def observed(attempt, event_type, *args, **kwargs):
            if event_type == 'item_left':
                self.assertIsNotNone(getattr(state, 'browser_entry_material', None))
            return event(attempt, event_type, *args, **kwargs)
        runtime['stream'].event = observed
        runner._browser_close(state, runtime, 'submit', 12)

    def test_old_submit_capability_preserves_source_leave_payload_and_full_entry_request(self):
        runner, state, runtime, calls, _ = self.fixture(capability=False)
        runner._submit_material(state, runtime, 0,
            {'payload': {'after_state_hash': 'hash'}}, list(range(11, 31)))
        self.assertEqual([name for name, _ in calls], ['submit_item', 'close_bundle', 'enter_item'])
        self.assertNotIn('next_entry', calls[1][1])
        left = next(row for row in calls[1][1]['capture_bundle']['events'] if row['event_type'] == 'item_left')
        self.assertIsNone(left['payload']['to_assignment_id'])

    def test_submit_partial_success_retries_only_target_entry(self):
        runner, state, runtime, calls, _ = self.fixture('partial')
        runner._submit_material(state, runtime, 0,
            {'payload': {'after_state_hash': 'hash'}}, list(range(11, 31)))
        self.assertEqual([name for name, _ in calls], ['submit_item', 'close_bundle', 'enter_item'])
        self.assertEqual(calls[2][1], {key: value for key, value in calls[1][1]['next_entry'].items() if key != 'assignment_id'})
        self.assertTrue(runtime['close_acknowledged'])
        self.assertEqual(runtime['lease'], {})

    def test_close_response_loss_uses_frozen_next_entry_and_original_receipt(self):
        runner, state, runtime, calls, entries = self.fixture('loss')
        with self.assertRaisesRegex(RuntimeError, 'response lost'):
            runner._browser_close(state, runtime, 'submit', 12)
        dto = runner._browser_close(state, runtime, 'submit', 12)
        self.assertIsNotNone(dto)
        runner.browser_open_question(state, 12, 'submit_next', dto)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(entries), 1)
        row = next(iter(runner.ledger['7']['capture_mutation_receipts'].values()))
        self.assertTrue(row['acknowledged'])
        body = calls[0][1]
        self.assertEqual(row['material_fingerprint'], digest({key: value for key, value in body.items()
            if key not in {'lease_token', 'client_instance_id'}}))

    def test_terminal_close_has_no_next_entry(self):
        runner, state, runtime, calls, _ = self.fixture()
        runner._browser_close(state, runtime, 'submit')
        self.assertNotIn('next_entry', calls[0][1])


if __name__ == '__main__':
    unittest.main()
