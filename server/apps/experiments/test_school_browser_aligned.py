import copy
import json
import sys
import unittest
import uuid
import time
import urllib.request
import urllib.error
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/load'))
import capture_protocol as protocol
import school_synchronized_180 as synchronized
from assessment_300 import Runner, StudentState


class SchoolBrowserAlignedTests(unittest.TestCase):
    def test_architecture_acceptance_refuses_missing_student_slice_capabilities(self):
        runner = synchronized.Synchronized180Runner('http://local', 3, 35, workload_version=protocol.BROWSER_ALIGNED_WORKLOAD)
        runner.require_student_slice_capabilities = True
        state = StudentState('fixture', 'fixture')
        state.session_id = 11
        with self.assertRaisesRegex(RuntimeError, 'Student slice capabilities'):
            runner.browser_open_question(state, 1, dto={'process': {}})

    def test_http_runner_unwraps_the_frozen_api_wire_contract(self):
        runner = Runner('http://localhost', 3, 35)
        state = StudentState('fixture', 'fixture')
        response = BytesIO(json.dumps({'data': {'session_id': 11}, 'meta': {'request_id': 'response-id'}}).encode())
        response.status = 200
        response.headers = {'Content-Type': 'application/json', 'X-Request-ID': 'response-id'}
        state.opener = SimpleNamespace(open=lambda *args, **kwargs: response)
        status, body, _ = runner.request(state, '/api/v1/student/tasks/3/start/', method='POST', body={})
        self.assertEqual(status, 200)
        self.assertEqual(body, {'session_id': 11})

    def test_http_runner_rejects_unversioned_success_body(self):
        runner = Runner('http://localhost', 3, 35)
        state = StudentState('fixture', 'fixture')
        response = BytesIO(b'{"session_id": 11}')
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        state.opener = SimpleNamespace(open=lambda *args, **kwargs: response)
        with self.assertRaisesRegex(RuntimeError, 'decoding failure'):
            runner.request(state, '/api/v1/student/tasks/3/start/', method='POST', body={})
        self.assertEqual(runner.request_errors, 1)

    def test_checkpoints_use_actual_ack_watermarks_and_freeze_original_document(self):
        self.assertTrue(hasattr(protocol, 'BrowserAlignedStream'), 'Browser-aligned immutable checkpoint stream is missing')
        stream = protocol.BrowserAlignedStream(7, 'client')
        events = [stream.event(11, 'pageshow', {'persisted': False}) for _ in range(3)]
        stream.accept_acknowledgements([{'event_id': events[2]['event_id'], 'client_sequence_no': 3}])
        checkpoint = stream.checkpoint('resume')
        self.assertEqual((checkpoint['highest_emitted_sequence_no'], checkpoint['highest_acked_sequence_no'],
                          checkpoint['highest_contiguous_acked_sequence_no'], checkpoint['pending_event_count']), (3, 3, 0, 2))
        self.assertIsNone(checkpoint['time_sync_id'])
        stream.accept_acknowledgements([{'event_id': event['event_id'], 'client_sequence_no': event['client_sequence_no']} for event in events[:2]])
        later = stream.checkpoint('ack_progress')
        self.assertEqual(later['highest_contiguous_acked_sequence_no'], 3)
        self.assertEqual(later['pending_event_count'], 0)
        self.assertEqual(stream.frozen_checkpoint(checkpoint['checkpoint_id']), checkpoint)
        changed = copy.deepcopy(checkpoint)
        changed['highest_contiguous_acked_sequence_no'] = 3
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            stream.validate_checkpoint(changed)

    def test_checkpoint_does_not_claim_unobserved_acknowledgement(self):
        self.assertTrue(hasattr(protocol, 'BrowserAlignedStream'), 'Actual ACK checkpoint accounting is missing')
        stream = protocol.BrowserAlignedStream(7, 'client')
        event = stream.event(11, 'pageshow', {'persisted': False})
        with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
            stream.accept_acknowledgements([{'event_id': str(uuid.uuid4()), 'client_sequence_no': event['client_sequence_no']}])
        self.assertEqual(stream.checkpoint('resume')['highest_acked_sequence_no'], 0)

    def workflow(self, atomic=False, failure_path=None, embedded_tools=False, saved_navigation=False, capture_bundle=False):
        self.assertTrue(hasattr(protocol.CaptureProtocolMixin, 'browser_aligned_answer'), 'Opt-in browser-aligned workflow is missing')
        class Harness(protocol.CaptureProtocolMixin, Runner):
            workload_version = 'browser-aligned-v2'
            atomic_submission = 'auto'
        runner = Harness('http://localhost', 3, 35, item_interval=0)
        runner.browser_shell_url = '/static/spa-assets/capture-shell-fixture.html'
        state = StudentState('PRIVATE-STUDENT', 'PRIVATE-PASSWORD')
        state.session_id, state.assignment_ids = 11, [1]
        calls, saved, versions, ingest, tools = [], {}, {}, {}, {}
        entry_counts = {}

        def tool_config(attempt):
            config = {'success': True, 'content_version': 'a' * 64, 'hints': [{'id': 'hint'}], 'rules': []}
            if (attempt - 21) % 5 == 0:
                rule = {'tool': 'check', 'required': True, 'id': 'rule'}
                if tools.get(attempt):
                    rule.update(pending_request=tools[attempt], already_presented=True)
                config['rules'] = [rule]
            return config

        def request(_, path, *, method='GET', body=None):
            calls.append((path, method, copy.deepcopy(body)))
            if failure_path and path == failure_path:
                raise RuntimeError('HTTP 500')
            route = path.split('?', 1)[0]
            if route.startswith('/api/v1/student/sessions/') and '/questions/' in route:
                index = int(route.strip('/').split('/')[-1])
                attempt, kind = 20 + index, protocol.QUESTION_TYPES[(index - 1) % 5]
                dto = {'assignment': {'assignment_id': index, 'attempt_id': attempt, 'status': 'in_progress'},
                    'session_id': 11, 'question': {'question_type': kind, 'snapshot_id': index,
                        'options': [{'key': 'true' if kind == 'true_false' else 'A'}, {'key': 'B'}]},
                    'initial_response_state': saved.get(attempt, {}).get('answer', [] if kind == 'multiple_choice' else ('' if kind in ('fill_blank', 'short_answer') else None)),
                    'navigation': [{'assignment_id': number} for number in range(1, 21)],
                    'pending_confidence': {'confidence_assignment_id': str(uuid.uuid5(uuid.NAMESPACE_OID, str(attempt))),
                        'scale_version': 'v2', 'prompt_shown_url': f'/confidence/{attempt}/'},
                    'actions': {'save_draft_url': f'/save/{attempt}/', 'submit_url': f'/submit/{attempt}/',
                        'research_tools_url': f'/tools/{attempt}/', 'finish_url': '/finish/'},
                    'process': {'capture_version': 'capture-v2', 'event_batch_url': '/events/', 'checkpoint_url': '/checkpoint/',
                        'time_sync_create_url': '/sync/create/', 'time_sync_finalize_url': '/sync/finalize/',
                        'atomic_submission': {'enabled': atomic, 'version': 1, 'max_events': 100, 'max_checkpoints': 100},
                        'active_response_event_id': saved.get(attempt, {}).get('target_event_id'), 'response_version': versions.get(attempt, 0),
                        'writer_lease_acquire_url': '/acquire/', 'writer_lease_renew_url': '/renew/', 'writer_lease_release_url': '/release/'}}
                entry_counts[attempt] = entry_counts.get(attempt, 0) + 1
                receipt = {'navigation_id': body['navigation_id'], 'session_id': 11, 'assignment_id': index,
                    'attempt_id': attempt, 'visit_id': len(calls), 'visit_index': entry_counts[attempt],
                    'entry_event_id': str(uuid.uuid4()), 'entered_at': body['client_occurred_at']}
                dto['entry_receipt'] = receipt
                dto['process'].update({key: receipt[key] for key in ('navigation_id', 'visit_id', 'visit_index', 'entry_event_id')})
                if embedded_tools:
                    dto['research_tools'] = tool_config(attempt)
                    if embedded_tools == 'without_rules':
                        dto['research_tools'].pop('rules')
                if saved_navigation:
                    dto['process']['save_and_leave'] = {'version': 1}
                if capture_bundle:
                    dto['process']['capture_bundle'] = {'enabled': True, 'version': 1, 'mutation_receipts': True,
                        'max_events': 100, 'max_checkpoints': 100, 'max_bytes': 8 * 1024 * 1024}
                return 200, dto, .001
            if route.startswith('/student/sessions/'):
                return 200, b'<html><head></head></html>', .001
            if route == runner.browser_shell_url:
                return 200, b'<html><script src="/static/spa-assets/fixture.js"></script></html>', .001
            if route == '/api/v1/auth/session/':
                return 200, {'user': {'id': 2}}, .001
            if route == '/events/':
                if body.get('capture_bundle'):
                    material = body['capture_bundle']
                    return 200, {**ack(material), 'committed': True,
                        'checkpoint_ids': [row['checkpoint_id'] for row in material['checkpoints']]}, .001
                return 200, ack(body), .001
            if route == '/checkpoint/':
                return 201, {'success': True, 'checkpoint_id': body['checkpoint_id']}, .001
            if route == '/sync/create/':
                self.assertIn('client_wall_time_utc', body)
                return 201, {'success': True, 'time_sync_id': str(uuid.uuid4())}, .001
            if route == '/sync/finalize/':
                self.assertIn('client_received_monotonic_ms', body)
                return 200, {'success': True, 'time_sync_id': body['time_sync_id'], 'sync_quality': 'valid'}, .001
            if route in ('/acquire/', '/renew/'):
                return 200, {'success': True, 'lease_token': 'lease', 'client_instance_id': body['client_instance_id'],
                             'expires_at': '2099-01-01T00:00:00+00:00'}, .001
            if route.startswith('/save/'):
                attempt = int(route.strip('/').split('/')[-1])
                if body.get('save_and_leave'):
                    bundle = body['save_and_leave']
                    self.assertEqual(bundle['expected_version'], versions.get(attempt, 0))
                    result = {'success': True, 'committed': True, 'navigation_token': body['navigation_token'],
                        'to_assignment_id': body['to_assignment_id'], 'writer_lease_released': True,
                        'active_response_event_id': body['target_event_id'], 'response_version': versions.get(attempt, 0),
                        'acknowledgements': ack(bundle)['acknowledgements'],
                        'checkpoint_ids': [row['checkpoint_id'] for row in bundle['checkpoints']]}
                    return 200, result, .001
                if body.get('capture_bundle'):
                    self.assertTrue(body.get('capture_operation_id'), 'Bundled draft must have an immutable operation identity')
                    uuid.UUID(body['capture_operation_id'])
                self.assertEqual(body['expected_version'], versions.get(attempt, 0))
                saved[attempt] = copy.deepcopy(body)
                versions[attempt] = versions.get(attempt, 0) + 1
                result = {'success': True, 'active_response_event_id': body['target_event_id'], 'response_version': versions[attempt]}
                if body.get('capture_bundle'):
                    result.update(committed=True, acknowledgements=ack(body['capture_bundle'])['acknowledgements'],
                        checkpoint_ids=[row['checkpoint_id'] for row in body['capture_bundle']['checkpoints']])
                return 200, result, .001
            if route.startswith('/submit/'):
                attempt = int(route.strip('/').split('/')[-1])
                index = attempt - 20
                result = {'success': True, 'status': 'submitted', 'next_url': f'/student/sessions/11/questions/{index + 1}/' if index < 20 else '/student/sessions/11/complete/'}
                if body.get('atomic_submission'):
                    bundle = body['atomic_submission']
                    self.assertEqual(bundle['expected_version'], versions.get(attempt, 0))
                    result.update(committed=True, acknowledgements=ack(bundle)['acknowledgements'],
                        checkpoint_ids=[row['checkpoint_id'] for row in bundle['checkpoints']],
                        final_response_event_id=body['target_event_id'], response_version=versions.get(attempt, 0) + 1)
                return 200, result, .001
            if route.startswith('/tools/'):
                attempt = int(route.strip('/').split('/')[-1])
                if not body:
                    return 200, tool_config(attempt), .001
                result = {'success': True, 'event_id': body['operation_id'], 'content_version': 'a' * 64}
                if body.get('capture_bundle'):
                    result.update(committed=True, acknowledgements=ack(body['capture_bundle'])['acknowledgements'],
                        checkpoint_ids=[row['checkpoint_id'] for row in body['capture_bundle']['checkpoints']])
                if body['stage'] == 'requested' and body['tool'] == 'check': tools[attempt] = result
                return 200, result, .001
            if route.endswith('/recovery/'):
                return 200, {'status': 'completed'}, .001
            if route.endswith('/summary/'):
                return 200, {'session_id': 11, 'status': 'completed', 'submission_count': 20, 'attempt_count': 20}, .001
            return 200, {'success': True}, .001

        def ack(body):
            return {'success': True, 'rejections': [], 'acknowledgements': [
                {'event_id': event['event_id'], 'client_sequence_no': event['client_sequence_no'],
                 'server_ingest_sequence_no': ingest.setdefault(event['event_id'], len(ingest) + 1)} for event in body['events']]}

        with patch.object(Runner, 'request', side_effect=request):
            if failure_path:
                with self.assertRaisesRegex(RuntimeError, 'HTTP 500'):
                    runner.answer(state)
            else:
                runner.answer(state)
        return runner, state, calls

    def test_twenty_item_scenario_matches_browser_next_return_and_submission_actions(self):
        runner, _, calls = self.workflow()
        protocol_summary = runner.protocol_summary()
        self.assertEqual(protocol_summary['workload_version'], 'browser-aligned-v2')
        self.assertEqual(protocol_summary['completed_sessions'], 1)
        actions = [row['action'] for row in protocol_summary['logical_actions']]
        self.assertEqual(actions.count('answer_input'), 20)
        self.assertEqual(actions.count('navigation_next_ready'), 19)
        self.assertEqual(actions.count('navigation_return_ready'), 19)
        self.assertEqual(actions.count('submit_confirmation_ready'), 20)
        self.assertEqual(actions.count('submit_next_ready'), 20)
        self.assertEqual(len([path for path, _, _ in calls if '/api/v1/student/sessions/' in path and '/questions/' in path]), 58)
        self.assertTrue(protocol_summary['all_events_acknowledged'])
        checkpoints = [body for path, _, body in calls if path == '/checkpoint/']
        self.assertTrue({'resume', 'ack_progress', 'navigation', 'commit', 'session_end'} <= {row['trigger'] for row in checkpoints})
        self.assertIsNone(next(row for row in checkpoints if row['trigger'] == 'resume')['time_sync_id'])
        self.assertTrue(all(row['time_sync_id'] is not None for row in checkpoints if row['trigger'] != 'resume'))
        self.assertFalse(any(path == '/finish/' for path, _, _ in calls))
        self.assertEqual(len([path for path, _, _ in calls if path == '/sync/create/']), 1)
        entries = [(method, body) for path, method, body in calls if '/questions/' in path and path.startswith('/api/')]
        self.assertTrue(all(method == 'POST' and body['navigation_id'] and body['client_occurred_at'] for method, body in entries))
        self.assertEqual(sum(row['trigger'] == 'resume' for row in checkpoints), 1)
        self.assertFalse(any(path.startswith('/student/sessions/') and '/questions/' in path for path, _, _ in calls))
        self.assertEqual(sum(path == runner.browser_shell_url for path, _, _ in calls), 1)
        self.assertEqual(sum(path == '/static/spa-assets/fixture.js' for path, _, _ in calls), 1)
        from jsonschema import Draft202012Validator, FormatChecker
        for _, _, body in calls:
            events = (body.get('atomic_submission', body).get('events', []) if body else [])
            for event in events:
                schema_path = ROOT / 'apps/assessment/event_schemas' / event['event_type'] / f"v{event['event_version']}.json"
                Draft202012Validator(json.loads(schema_path.read_text(encoding='utf-8')),
                    format_checker=FormatChecker()).validate(event['payload'])

    def test_capture_bundle_keeps_facts_and_workload_while_reducing_requests(self):
        from collections import Counter
        baseline, _, old_calls = self.workflow(atomic=True, saved_navigation=True, embedded_tools=True)
        candidate, _, calls = self.workflow(atomic=True, saved_navigation=True, embedded_tools=True, capture_bundle=True)
        bundled = [body['capture_bundle'] for _, _, body in calls if body and body.get('capture_bundle')]
        self.assertTrue(bundled, 'Protocol must use the declared capture bundle capability')
        self.assertLess(len(calls), len(old_calls))
        for runner in (baseline, candidate):
            self.assertEqual(runner.protocol_summary()['completed_sessions'], 1)
            self.assertTrue(runner.protocol_summary()['all_events_acknowledged'])
            self.assertTrue(runner.protocol_summary()['all_checkpoints_acknowledged'])
            self.assertEqual(len(runner.ledger['11']['visits']), 58)
        def facts(runner):
            row = runner.ledger['11']
            return (Counter(event['event_type'] for event in row['events'].values()),
                    Counter(item['trigger'] for item in row['checkpoints'].values()))
        self.assertEqual(facts(baseline), facts(candidate))
        self.assertEqual([(row['action'], row['item_index']) for row in baseline.protocol_summary()['logical_actions']],
                         [(row['action'], row['item_index']) for row in candidate.protocol_summary()['logical_actions']])
        self.assertTrue(all(bundle['version'] == 1 and len(bundle['events']) <= 100
                            and len(bundle['checkpoints']) <= 100 for bundle in bundled))

    def test_mutation_receipts_account_for_unknown_commits_and_exact_ack_ordinals(self):
        from apps.experiments.management.commands.verify_school_protocol_load import capture_mutation_receipt_checks
        token, event_id, checkpoint_id = (str(uuid.uuid4()) for _ in range(3))
        receipt = {'committed': True, 'active_response_event_id': event_id,
                   'acknowledgements': [{'event_id': event_id, 'client_sequence_no': 1, 'server_ingest_sequence_no': 7}],
                   'checkpoint_ids': [checkpoint_id]}
        planned = {'attempt_id': 21, 'kind': 'draft', 'material_fingerprint': 'a' * 64,
                   'event_identities': {'1': event_id}, 'checkpoint_ids': [checkpoint_id],
                   'target_event_id': event_id, 'acknowledged': True, 'receipt_sha256': protocol.digest(receipt)}
        ledger = {'sessions': {'11': {'capture_mutation_receipts': {token: planned}}}}
        stored = SimpleNamespace(token=token, attempt_id=21, kind='draft', material_fingerprint='a' * 64, receipt=receipt)
        event = SimpleNamespace(pk=event_id, client_sequence_no=1, server_ingest_sequence_no=7)
        result = capture_mutation_receipt_checks(ledger, [stored], [event])
        self.assertTrue(result['all_capture_mutation_receipts_confirmed_and_matched'])
        planned['acknowledged'] = False
        planned['receipt_sha256'] = None
        self.assertTrue(capture_mutation_receipt_checks(ledger, [stored], [event])['known_capture_mutation_integrity_passed'])
        self.assertFalse(capture_mutation_receipt_checks(ledger, [stored], [event])['all_capture_mutation_receipts_confirmed_and_matched'])
        stored.receipt = copy.deepcopy(receipt)
        stored.receipt['acknowledgements'][0]['server_ingest_sequence_no'] = 99
        self.assertFalse(capture_mutation_receipt_checks(ledger, [stored], [event])['known_capture_mutation_integrity_passed'])
        stored.receipt = receipt
        planned['acknowledged'] = True
        self.assertFalse(capture_mutation_receipt_checks(ledger, [], [event])['known_capture_mutation_integrity_passed'])
        self.assertFalse(capture_mutation_receipt_checks({'sessions': {}}, [stored], [event])['known_capture_mutation_integrity_passed'])

    def test_feedback_is_confirmed_in_the_submitted_attempt_close_boundary(self):
        _, _, calls = self.workflow(atomic=True, saved_navigation=True)
        batches = [body['events'] for path, _, body in calls if path == '/events/']
        feedback = [event for batch in batches for event in batch if event['event_type'] == 'feedback_shown']
        self.assertEqual(len(feedback), 20)
        for event in feedback:
            batch = next(batch for batch in batches if event in batch)
            closes = [row for row in batch if row['attempt_id'] == event['attempt_id']
                      and row['event_type'] == 'item_left' and row['payload']['leave_reason'] == 'submit']
            self.assertEqual(len(closes), 1)
            self.assertLess(event['client_sequence_no'], closes[0]['client_sequence_no'])

    def test_atomic_and_legacy_keep_logical_actions_but_atomic_reduces_http_calls(self):
        baseline, _, baseline_calls = self.workflow()
        candidate, _, candidate_calls = self.workflow(atomic=True)
        def actions(runner):
            return [(row['action'], row['item_index']) for row in runner.protocol_summary()['logical_actions']]
        self.assertEqual(actions(baseline), actions(candidate))
        self.assertTrue(candidate.protocol_summary()['all_events_acknowledged'])
        self.assertLess(len(candidate_calls), len(baseline_calls))
        bundles = [body['atomic_submission'] for path, _, body in candidate_calls if path.startswith('/submit/')]
        self.assertEqual(len(bundles), 20)
        self.assertTrue(all(any(row['trigger'] == 'commit' for row in bundle['checkpoints']) for bundle in bundles))

    def test_saved_navigation_preserves_actions_and_checkpoints_with_one_leave_request(self):
        baseline, _, old_calls = self.workflow()
        runner, _, calls = self.workflow(saved_navigation=True)
        self.assertEqual(runner.protocol_summary()['completed_sessions'], 1)
        self.assertEqual(runner.protocol_summary()['logical_actions'], baseline.protocol_summary()['logical_actions'])
        self.assertTrue(runner.protocol_summary()['all_events_acknowledged'])
        self.assertTrue(runner.protocol_summary()['all_checkpoints_acknowledged'])
        leaves = [body for _, _, body in calls if body and body.get('save_and_leave')]
        self.assertEqual(len(leaves), 38)
        self.assertLess(len(calls), len(old_calls))
        self.assertEqual(sum(path == '/release/' for path, _, _ in calls), 20)
        self.assertEqual(sum(path == '/acquire/' for path, _, _ in calls), 39)
        self.assertTrue(all(body['save_and_leave']['events'][-1]['correlation_id'] == body['navigation_token'] for body in leaves))

    def test_embedded_rules_reuse_authorized_dto_configuration_without_mount_posts(self):
        baseline, _, baseline_calls = self.workflow()
        candidate, _, candidate_calls = self.workflow(embedded_tools=True)
        baseline_config = [body for path, _, body in baseline_calls if path.startswith('/tools/') and body == {}]
        candidate_config = [body for path, _, body in candidate_calls if path.startswith('/tools/') and body == {}]
        self.assertEqual(len(baseline_config), 58)
        self.assertEqual(len(candidate_config), 0)
        self.assertEqual(baseline.protocol_summary()['logical_actions'], candidate.protocol_summary()['logical_actions'])
        from jsonschema import Draft202012Validator, FormatChecker
        for _, _, body in candidate_calls:
            for event in body.get('events', []) if body else []:
                schema = ROOT / 'apps/assessment/event_schemas' / event['event_type'] / f"v{event['event_version']}.json"
                Draft202012Validator(json.loads(schema.read_text(encoding='utf-8')), format_checker=FormatChecker()).validate(event['payload'])

    def test_older_dto_without_rules_keeps_tools_configuration_posts(self):
        runner, _, calls = self.workflow(embedded_tools='without_rules')
        self.assertEqual(sum(path.startswith('/tools/') and body == {} for path, _, body in calls), 58)
        self.assertEqual(runner.protocol_summary()['completed_sessions'], 1)

    def test_persistence_ledger_preserves_immutable_checkpoints_clock_samples_and_visits(self):
        runner, _, _ = self.workflow()
        ledger = runner.ledger['11']
        self.assertEqual(len(ledger['visits']), 58)
        self.assertEqual(len(ledger['time_syncs']), 1)
        for checkpoint_id, row in ledger['checkpoints'].items():
            self.assertEqual(row['document']['checkpoint_id'], checkpoint_id)
            self.assertEqual(protocol.digest(row['document']), row['payload_sha256'])
        for sample_id, row in ledger['time_syncs'].items():
            self.assertEqual(row['finalize']['time_sync_id'], sample_id)
            self.assertTrue(row['finalized'])
        self.assertTrue(all(row.get('time_sync_id') in ledger['time_syncs'] for row in ledger['events'].values()
                            if row['event_type'] not in {'pageshow', 'document_visible', 'window_focused'}))
        self.assertTrue(all(row['time_sync_id'] is None for row in ledger['events'].values()
                            if row['event_type'] in {'pageshow', 'document_visible', 'window_focused'}))

    def test_spa_visits_reuse_session_collector_clock_and_confirmed_head(self):
        runner, _, calls = self.workflow()
        visits = runner.ledger['11']['visits']
        self.assertEqual(len({row['client_instance_id'] for row in visits}), 1)
        events = [row for _, _, body in calls if body for row in body.get('events', [])]
        self.assertEqual(len({row['client_time_origin'] for row in events}), 1)
        rendered = [row for row in events if row['event_type'] == 'item_rendered']
        self.assertEqual([row['correlation_id'] for row in rendered], [row['navigation_id'] for row in visits])
        for client in {row['client_instance_id'] for row in visits}:
            ledger_events = [row for row in runner.ledger['11']['events'].values() if row['client_instance_id'] == client]
            self.assertEqual(min(row['client_sequence_no'] for row in ledger_events), 1)
        transitions = [row for row in events if row['event_type'] == 'response_transition']
        self.assertEqual(len(transitions), 20)
        self.assertTrue(all(row['payload']['parent_event_id'] is None for row in transitions))

    def test_http_failure_records_only_safe_server_code_and_preserves_duration(self):
        class Harness(protocol.CaptureProtocolMixin, Runner):
            workload_version = 'browser-aligned-v2'
        runner = Harness('http://localhost', 3, 35, item_interval=0)
        state = StudentState('PRIVATE-STUDENT', 'PRIVATE-PASSWORD')
        original = SimpleNamespace(open=lambda *args, **kwargs: None)
        state.opener = original

        def fail(*args, **kwargs):
            raise urllib.error.HTTPError('http://localhost/events/', 400, 'Bad Request', {},
                BytesIO(json.dumps({'error': {'code': 'invalid_event', 'message': 'PRIVATE-ANSWER'}}).encode()))
        original.open = fail
        with self.assertRaisesRegex(RuntimeError, 'HTTP 400'):
            runner._browser_request(state, '/events/', 'event_batch', method='POST', body={'events': []})
        self.assertIs(state.opener, original)
        measurement = runner.protocol_summary()['operation_measurements'][-1]
        self.assertEqual(measurement['server_error_code'], 'invalid_event')
        self.assertGreaterEqual(measurement['elapsed_seconds'], 0)
        self.assertNotIn('PRIVATE', json.dumps(runner.protocol_summary()))
        original.open = lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.HTTPError(
            'http://localhost/events/', 400, 'Bad Request', {}, BytesIO(b'{"error":{"code":"PRIVATE-ANSWER"}}')))
        with self.assertRaises(RuntimeError):
            runner._browser_request(state, '/events/', 'event_batch', method='POST', body={'events': []})
        self.assertEqual(runner.protocol_summary()['operation_measurements'][-1]['server_error_code'], 'unrecognized_error_code')

    def test_acknowledged_event_payloads_retire_without_losing_identity_or_checkpoint_facts(self):
        stream = protocol.BrowserAlignedStream(7)
        first = stream.event(21, 'document_visible', {})
        second = stream.event(21, 'window_focused', {})
        ack = {'event_id': first['event_id'], 'client_sequence_no': 1}
        stream.accept_acknowledgements([ack])
        self.assertNotIn(1, stream.events)
        self.assertEqual(stream.pending_events(), [second])
        stream.accept_acknowledgements([ack])
        checkpoint = stream.checkpoint('ack_progress')
        self.assertEqual((checkpoint['highest_emitted_sequence_no'], checkpoint['highest_acked_sequence_no'],
                          checkpoint['pending_event_count']), (2, 1, 1))
        with self.assertRaisesRegex(RuntimeError, 'Unobserved'):
            stream.accept_acknowledgements([{'event_id': 'wrong', 'client_sequence_no': 1}])

    def test_browser_input_wire_facts_include_choice_metadata_and_text_observations(self):
        _, _, calls = self.workflow()
        events = [event for _, _, body in calls if body for event in body.get('events', [])]
        choices = [event for event in events if event['event_type'] == 'response_transition'
            and event['payload']['after_state']['question_type'] in ('single_choice', 'multiple_choice', 'true_false')]
        self.assertTrue(all(event['payload']['input_type'] == 'select' for event in choices))
        self.assertTrue(all(event['payload']['option_action'] == 'select' for event in choices))
        self.assertEqual(sum(event['event_type'] == 'text_edit_observed' for event in events), 8)
        self.assertEqual(sum(event['event_type'] == 'input_operation' for event in events), 8)

    def test_failed_checkpoint_retains_duration_and_fails_session(self):
        runner, _, _ = self.workflow(failure_path='/checkpoint/')
        self.assertGreaterEqual(len(runner.metrics.get('checkpoint', [])), 1)
        self.assertEqual(runner.protocol_summary()['completed_sessions'], 0)

    def test_real_ten_second_tools_poll_is_included_in_eighteen_second_wait(self):
        class Harness(protocol.CaptureProtocolMixin, Runner):
            workload_version = protocol.BROWSER_ALIGNED_WORKLOAD
        runner = Harness('http://localhost', 3, 35, item_interval=18)
        runner.browser_answer_origin, runner.browser_answer_wall_origin = 0, 1700000000
        state = SimpleNamespace(browser_current={'heartbeat_due': 30, 'tools_refresh_due': 10,
            'dto': {'actions': {'research_tools_url': '/tools/'}}})
        now, calls = [0], []
        with patch.object(protocol.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(protocol.time, 'sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), \
                patch.object(runner, '_browser_post', side_effect=lambda *args: calls.append(args[1:])):
            runner.browser_before_item(state, 1)
        self.assertEqual(calls, [('/tools/', {}, 'tools_refresh')])
        self.assertEqual(now[0], 18)

    def test_failed_navigation_retains_complete_action_duration(self):
        runner, _, _ = self.workflow(failure_path='/release/')
        failures = [row for row in runner.protocol_summary()['operation_measurements']
            if row['metric'] == 'navigation_next_ready' and not row['successful']]
        self.assertEqual(len(failures), 1)
        self.assertGreaterEqual(failures[0]['elapsed_seconds'], 0)

    def test_new_workload_acceptance_requires_all_actions_and_checkpoint_acks(self):
        runner, _, _ = self.workflow()
        summary = runner.protocol_summary()
        result = {'student_count': 1, 'metrics': {key: {'p95_seconds': 0, 'p99_seconds': 0} for key in runner.metrics},
            'capture_protocol': summary, 'errors': [], 'http_error_count': 0, 'session_count': 1, 'unique_session_count': 1}
        result['metrics']['start'] = {'p95_seconds': 0, 'p99_seconds': 0}
        result['metrics']['start_to_first_question_ready'] = {'p95_seconds': 0, 'p99_seconds': 0}
        result['metrics']['finish_ready'] = {'p95_seconds': 0, 'p99_seconds': 0}
        self.assertTrue(all(synchronized.browser_aligned_checks(result).values()))
        result['metrics']['navigation_next_ready']['p95_seconds'] = 2.1
        self.assertFalse(synchronized.browser_aligned_checks(result)['navigation_next_ready_p95'])
        result['metrics']['navigation_next_ready']['p95_seconds'] = 0
        summary['logical_actions'].pop()
        self.assertFalse(synchronized.browser_aligned_checks(result)['frozen_logical_probe_actions'])
        summary['all_checkpoints_acknowledged'] = False
        self.assertFalse(synchronized.browser_aligned_checks(result)['capture_protocol_complete'])

    def test_absolute_arrival_schedule_catches_up_without_resetting_after_slow_item(self):
        class Harness(protocol.CaptureProtocolMixin, Runner):
            workload_version = 'browser-aligned-v2'
        runner = Harness('http://localhost', 3, 35, item_interval=18)
        self.assertTrue(hasattr(runner, 'browser_before_item'), 'Anchored browser-aligned arrival schedule is missing')
        state = SimpleNamespace(load_student_index=5)
        runner.browser_answer_origin = 100
        runner.browser_answer_wall_origin = 1000
        with patch.object(protocol.time, 'monotonic', side_effect=[100, 140]), patch.object(protocol.time, 'sleep') as sleeping:
            runner.browser_before_item(state, 0)
            runner.browser_before_item(state, 1)
        sleeping.assert_not_called()
        arrival = runner.item_arrivals[-1]
        self.assertEqual(arrival['planned_at'], 1018)
        self.assertEqual(arrival['actual_offset_seconds'], 40)
        self.assertEqual(arrival['generator_lateness_seconds'], 22)

    def test_synchronized_runner_requires_explicit_workload_version(self):
        runner = synchronized.Synchronized180Runner('http://localhost', 3, 35, item_interval=18)
        self.assertEqual(getattr(runner, 'workload_version', protocol.CONTRACT), protocol.CONTRACT)
        self.assertTrue(hasattr(synchronized, 'build_parser'), 'Explicit workload CLI selector is missing')
        parser = synchronized.build_parser()
        args = parser.parse_args(['--manifest', 'fixture.json', '--base-url', 'http://localhost', '--output', 'result.json',
            '--workload-version', 'browser-aligned-v2', '--atomic-submission', 'off'])
        runner = synchronized.Synchronized180Runner('http://localhost', 3, args.timeout, item_interval=18,
            workload_version=args.workload_version, atomic_submission=args.atomic_submission)
        self.assertEqual(runner.workload_version, 'browser-aligned-v2')
        self.assertEqual(runner.atomic_submission, 'off')

    def test_browser_aligned_orchestration_does_not_add_legacy_resume_http_action(self):
        runner = SimpleNamespace(workload_version='browser-aligned-v2', phase=lambda name, states, operation: phases.append(name),
            prepare_login=lambda state: None, authenticate=lambda state: None, start_and_first_question=lambda state: None,
            resume=lambda state: None, answer=lambda state: None)
        phases = []
        state = SimpleNamespace(opener=SimpleNamespace())
        synchronized.run_synchronized(runner, [state])
        self.assertEqual(phases, ['login_preparation', 'login_post', 'start', 'answer'])

    def test_initialization_posts_do_not_inflate_synchronized_start_attempt_count(self):
        runner = synchronized.Synchronized180Runner('http://localhost', 3, 35, workload_version='browser-aligned-v2')
        runner.phase_plans['start'] = {'wall': time.time(), 'perf': time.perf_counter()}
        runner.request_context.current = {'operation_id': 'op', 'attempt': 1}
        response = SimpleNamespace(status=200)
        opener = synchronized.ObservedOpener(SimpleNamespace(open=lambda request, timeout: response), runner,
            SimpleNamespace(load_stage='start', load_student_index=0))
        opener.open(urllib.request.Request('http://localhost/api/v1/student/tasks/3/start/', method='POST'), 35)
        opener.open(urllib.request.Request('http://localhost/assessment/process/writer-lease/acquire/', method='POST'), 35)
        opener.open(urllib.request.Request('http://localhost/student/sessions/11/questions/1/'), 35)
        self.assertEqual([row['phase'] for row in runner.synchronized_sends], ['start'])


if __name__ == '__main__':
    unittest.main()
