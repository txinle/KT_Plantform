import copy
import json
import sys
import tempfile
import signal
import threading
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase


class SchoolCaptureProtocolTests(SimpleTestCase):
    def test_sigterm_flushes_partial_material_before_waiting_for_http_threads(self):
        module = self.module()
        before = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'fixture.json'
            output = Path(directory) / 'http.json'
            manifest.write_text(json.dumps({'task_id': 9, 'student_count': 180,
                'capture_protocol': module.CONTRACT, 'source_config': {'frozen': True}}))
            runner = SimpleNamespace(lock=threading.Lock(), ledger={'7': {'events': {
                'event': {'acknowledged': True, 'payload_sha256': 'original'}}}},
                workload_version=module.BROWSER_ALIGNED_WORKLOAD, budget_deadline=None,
                budget_interrupted=False)
            with module.evidence_files(manifest, output) as files:
                files['runner'] = runner
                handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler), 'SIGTERM must preserve evidence before Docker stops the generator')
                with self.assertRaises(SystemExit):
                    handler(signal.SIGTERM, None)
                # Read before context exit: ThreadPoolExecutor can still be waiting on HTTP.
                self.assertTrue(json.loads(output.read_text())['incomplete'])
                ledger = json.loads(manifest.with_suffix('.ledger.json').read_text())
                self.assertEqual(ledger['sessions'], runner.ledger)
                self.assertTrue(ledger['incomplete'])
                self.assertEqual(ledger['interruption_signal'], signal.SIGTERM)
                self.assertTrue(runner.budget_interrupted)
                self.assertLessEqual(runner.budget_deadline, module.time.monotonic())
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def module(self):
        sys.path.insert(0, str(Path(settings.BASE_DIR) / 'scripts/load'))
        import capture_protocol
        return capture_protocol

    def browser_bundle_harness(self):
        module = self.module()
        from assessment_300 import Runner, StudentState
        class Harness(module.CaptureProtocolMixin, Runner):
            workload_version = module.BROWSER_ALIGNED_WORKLOAD
        runner = Harness('http://localhost', 3, 35)
        state = StudentState('private', 'private')
        state.session_id = 11
        runner.ledger['11'] = {'events': {}, 'event_ids': [], 'answers': {}, 'submission_tokens': []}
        runtime = {'stream': module.BrowserAlignedStream(11, 'client'), 'head': 'head', 'version': 2,
            'lease': {'writer_lease_token': 'lease', 'writer_client_instance_id': 'client'},
            'dto': {'assignment': {'attempt_id': 21}, 'process': {'event_batch_url': '/events/',
                'writer_lease_release_url': '/release/', 'capture_bundle': {'enabled': True, 'version': 1},
                'close_bundle': {'enabled': True, 'version': 1}}}}
        calls = []
        def request(_, path, *, method='GET', body=None):
            calls.append((path, copy.deepcopy(body)))
            bundle = body.get('capture_bundle', body)
            return 200, {'success': True, 'committed': True,
                'capture_operation_id': body.get('capture_operation_id'), 'writer_lease_released': True,
                'status': 'submitted', 'response_version': 2, 'active_response_event_id': 'head',
                'final_response_event_id': 'head', 'rejections': [],
                'checkpoint_ids': [row['checkpoint_id'] for row in bundle.get('checkpoints', [])],
                'acknowledgements': [{'event_id': row['event_id'], 'client_sequence_no': row['client_sequence_no'],
                    'server_ingest_sequence_no': row['client_sequence_no']} for row in bundle.get('events', [])]}, .001
        return runner, state, runtime, calls, request, Runner

    def test_bundle_flush_defers_new_ack_checkpoint_until_next_frozen_round(self):
        runner, state, runtime, calls, request, base = self.browser_bundle_harness()
        stream = runtime['stream']
        stream.event(21, 'item_entered', {})
        checkpoint = stream.checkpoint('resume')
        with patch.object(base, 'request', side_effect=request):
            runner._browser_flush(state, runtime)
            self.assertEqual(len(calls), 1, 'ACK progress must not add a second blocking HTTP request')
            pending = [row for key, row in stream.checkpoints.items() if key not in stream.acked_checkpoint_ids]
            self.assertEqual([row['trigger'] for row in pending], ['ack_progress'])
            self.assertIn(checkpoint['checkpoint_id'], stream.acked_checkpoint_ids)
            self.assertEqual(pending[0]['highest_acked_sequence_no'], 1)
            frozen = copy.deepcopy(pending[0])
            runner._browser_flush(state, runtime)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1]['capture_bundle']['events'], [])
        self.assertEqual(calls[1][1]['capture_bundle']['checkpoints'], [frozen])
        self.assertEqual(set(stream.checkpoints), stream.acked_checkpoint_ids)

    def test_submit_close_combines_actual_leave_checkpoint_and_release_with_immutable_replay(self):
        runner, state, runtime, calls, request, base = self.browser_bundle_harness()
        stream = runtime['stream']
        stream.event(21, 'feedback_shown', {'feedback_mode': 'none'})
        with patch.object(base, 'request', side_effect=request):
            runner._browser_close(state, runtime, 'submit')
            self.assertEqual([path for path, _ in calls], ['/release/'])
            body = calls[0][1]
            left = next(row for row in body['capture_bundle']['events'] if row['event_type'] == 'item_left')
            self.assertEqual(body['leave_event_id'], left['event_id'])
            self.assertEqual(left['payload']['leave_reason'], 'submit')
            self.assertEqual([row['trigger'] for row in body['capture_bundle']['checkpoints']], ['session_end'])
            self.assertEqual(body['lease_token'], 'lease')
            sequence = stream.sequence
            runner._browser_close(state, runtime, 'submit')
            self.assertEqual(calls[1], calls[0])
            self.assertEqual(stream.sequence, sequence)
            runner._browser_flush(state, runtime)
        self.assertEqual(len(calls), 3)
        self.assertEqual(set(stream.checkpoints), stream.acked_checkpoint_ids)
        planned = runner.ledger['11']['capture_mutation_receipts'][body['capture_operation_id']]
        self.assertEqual(planned['kind'], 'close')
        self.assertEqual(planned['leave_event_id'], left['event_id'])
        self.assertEqual(planned['material_fingerprint'], self.module().digest({key: value for key, value in body.items()
            if key not in {'lease_token', 'client_instance_id'}}))

    def test_close_lost_receipt_replays_frozen_material_without_new_leave_event(self):
        runner, state, runtime, calls, request, base = self.browser_bundle_harness()
        def lost_reply(*args, **kwargs):
            request(*args, **kwargs)
            raise RuntimeError('HTTP 500')
        with patch.object(base, 'request', side_effect=lost_reply):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 500'):
                runner._browser_close(state, runtime, 'submit')
        with patch.object(base, 'request', side_effect=request):
            runner._browser_close(state, runtime, 'submit')
        self.assertEqual(calls[1], calls[0])
        self.assertEqual(runtime['stream'].sequence, 1)

    def test_close_verifier_rejects_unreleased_lease_or_wrong_operation(self):
        from apps.experiments.management.commands.verify_school_protocol_load import capture_mutation_receipt_checks
        token, event_id = 'token', 'left'
        receipt = {'success': True, 'committed': True, 'capture_operation_id': token, 'writer_lease_released': True,
            'status': 'submitted', 'response_version': 2, 'acknowledgements': [
                {'event_id': event_id, 'client_sequence_no': 1, 'server_ingest_sequence_no': 7}], 'checkpoint_ids': []}
        planned = {'attempt_id': 21, 'kind': 'close', 'material_fingerprint': 'a' * 64,
            'event_identities': {'1': event_id}, 'checkpoint_ids': [], 'leave_event_id': event_id,
            'target_event_id': None, 'acknowledged': False, 'receipt_sha256': None}
        ledger = {'sessions': {'11': {'capture_mutation_receipts': {token: planned}}}}
        stored = SimpleNamespace(token=token, attempt_id=21, kind='close', material_fingerprint='a' * 64, receipt=receipt)
        event = SimpleNamespace(pk=event_id, attempt_id=21, event_type='item_left', client_sequence_no=1, server_ingest_sequence_no=7)
        self.assertTrue(capture_mutation_receipt_checks(ledger, [stored], [event])['known_capture_mutation_integrity_passed'])
        for changed in ({'writer_lease_released': False}, {'capture_operation_id': 'other'}):
            stored.receipt = {**receipt, **changed}
            self.assertFalse(capture_mutation_receipt_checks(ledger, [stored], [event])['known_capture_mutation_integrity_passed'])

    def test_five_type_trajectories_keep_modification_clear_and_return(self):
        module = self.module()
        from apps.assessment.event_schema_registry import validate_event_payload
        from apps.assessment.response_snapshot_services import ResponseState
        for kind in module.QUESTION_TYPES:
            with self.subTest(kind=kind):
                stream = module.ProtocolStream(11, 'client')
                question = {'question_type': kind, 'options': [{'key': 'A'}, {'key': 'B'}]}
                values = module.answer_values(question)
                empty = [] if kind == 'multiple_choice' else ('' if kind in ('fill_blank', 'short_answer') else None)
                events = stream.transitions(12, kind, empty, None, values)
                self.assertEqual(len(events), len(values))
                self.assertEqual(events[0]['payload']['after_state'], events[2]['payload']['after_state'])
                self.assertEqual(events[3]['payload']['after_state']['value'], empty)
                parent = None
                for event in events:
                    payload = event['payload']
                    validate_event_payload('response_transition', 1, payload)
                    self.assertEqual(payload['parent_event_id'], parent)
                    for side in ('before', 'after'):
                        self.assertEqual(payload[f'{side}_state_hash'], ResponseState(**payload[f'{side}_state']).state_hash)
                    parent = event['event_id']

    def test_generated_ime_protocol_records_only_confirmed_content_as_response(self):
        module = self.module()
        from apps.assessment.event_schema_registry import validate_event_payload
        stream = module.ProtocolStream(11, 'client')
        operations = stream.composition_operations(12, None)
        for event in operations:
            validate_event_payload('input_operation', 1, event['payload'])
            self.assertNotIn('zhong', json.dumps(event))
        self.assertEqual([event['payload']['composition'] for event in operations], ['started', 'updating', 'confirmed'])
        transition = stream.transitions(12, 'short_answer', '', None, ['中文'])[0]
        self.assertEqual(transition['payload']['branch_id'], operations[0]['payload']['branch_id'])

    def test_ack_accounting_rejects_missing_duplicate_or_changed_ingest_and_preserves_ledger(self):
        module = self.module()
        stream = module.ProtocolStream(11, 'client')
        event = stream.transitions(12, 'short_answer', '', None, ['中文'])[0]
        body = {'client_instance_id': 'client', 'events': [event]}
        ledger = {}
        module.emitted(ledger, body)
        ack = {'event_id': event['event_id'], 'client_sequence_no': 1, 'server_ingest_sequence_no': 4}
        for acks in ([], [ack, ack], [{**ack, 'event_id': 'different'}]):
            with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
                module.acknowledged(ledger, body, {'success': True, 'acknowledgements': acks, 'rejections': []})
        module.acknowledged(ledger, body, {'success': True, 'acknowledgements': [ack], 'rejections': []})
        module.emitted(ledger, body)
        module.acknowledged(ledger, body, {'success': True, 'acknowledgements': [ack], 'rejections': []})
        row = ledger['11']['events'][event['event_id']]
        self.assertEqual(row['emission_count'], 2)
        self.assertEqual(row['ack_count'], 2)
        self.assertTrue(row['acknowledged'])
        self.assertNotIn('中文', json.dumps(ledger, ensure_ascii=False))
        with self.assertRaisesRegex(RuntimeError, 'acknowledgement'):
            module.acknowledged(ledger, body, {'success': True, 'acknowledgements': [{**ack, 'server_ingest_sequence_no': 5}], 'rejections': []})
        changed = copy.deepcopy(body)
        changed['events'][0]['payload']['input_type'] = 'changed'
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            module.emitted(ledger, changed)

    def test_projection_hash_allows_only_documented_server_enrichment(self):
        module = self.module()
        source = {'capture_version': 'capture-v2', 'data': {'a': 1}}
        enriched = {**source, 'snapshot_id': 'uuid'}
        self.assertEqual(module.project_payload(enriched, list(source), 'research_tool_requested'), source)
        with self.assertRaisesRegex(ValueError, 'enrichment'):
            module.project_payload({**enriched, 'surprise': True}, list(source), 'research_tool_requested')
        with self.assertRaisesRegex(ValueError, 'fields'):
            module.project_payload({'data': {}}, list(source), 'research_tool_requested')

    def test_real_dto_empty_states_and_short_snapshot_ref_are_decoded(self):
        module = self.module()
        for kind, empty in (('multiple_choice', []), ('single_choice', None), ('fill_blank', ''), ('short_answer', '')):
            self.assertEqual(module.response_value({'question': {'question_type': kind}, 'initial_response_state': None, 'draft_answer': None}), empty)
        dto = {'question': {'question_type': 'short_answer'}, 'draft_answer': '中文',
            'initial_response_state': {'snapshot_id': 'uuid', 'state_hash': module.digest({'question_type': 'short_answer', 'value': '中文'})}}
        self.assertEqual(module.response_value(dto), '中文')
        dto['initial_response_state']['state_hash'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'hash'):
            module.response_value(dto)

    def test_short_runner_lease_before_events_initializes_answer_ledger(self):
        self.module()
        from school_short_storm import ProtocolShortRunner, Runner, StudentState
        runner = ProtocolShortRunner('http://localhost', 3, 35)
        state = StudentState('private', 'private')
        state.session_id = 11
        with patch.object(Runner, 'request', return_value=(200, {'success': True}, .01)):
            runner.request(state, '/acquire/', method='POST', body={'client_instance_id': 'client'})
        self.assertIn('answers', runner.ledger['11'])

    def test_evidence_reserves_task_and_all_files_before_work_and_keeps_failure(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / 'fixture.json'
            manifest.write_text(json.dumps({'task_id': 3, 'run_id': 'run'}))
            output = directory / 'result.json'
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                with module.evidence_files(manifest, output) as files:
                    self.assertTrue(output.exists())
                    self.assertTrue(output.with_suffix('.attempts.jsonl').exists())
                    self.assertTrue(manifest.with_suffix('.ledger.json').exists())
                    raise RuntimeError('failed SECRET')
            result = json.loads(output.read_text())
            self.assertFalse(result['passed'])
            self.assertNotIn('SECRET', output.read_text())
            with self.assertRaises(FileExistsError):
                with module.evidence_files(manifest, directory / 'new-result.json'):
                    self.fail('Previously claimed task replayed')
            self.assertEqual(json.loads(output.read_text()), result)

    def test_existing_output_rejected_without_consuming_task(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / 'fixture.json'
            manifest.write_text(json.dumps({'task_id': 3, 'run_id': 'run'}))
            output = directory / 'result.json'
            output.write_text('OLD FAILURE')
            with self.assertRaises(FileExistsError):
                with module.evidence_files(manifest, output):
                    self.fail('Existing output overwritten')
            self.assertEqual(output.read_text(), 'OLD FAILURE')
            self.assertFalse((directory / '.school-task-3.claim.json').exists())

    def test_mixed_seed_output_collision_rejected_before_seed_mutation(self):
        from apps.experiments.management.commands.seed_school_mixed_load import Command
        from django.db import connection
        from django.core.management.base import CommandError
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'same-baseline-history.json').write_text('OLD FAILURE')
            command = Command()
            with patch.dict(connection.settings_dict, NAME='kt_e2e'), patch.object(command, '_seed') as seed:
                with self.assertRaisesRegex(CommandError, 'overwrite'):
                    command.handle(output_dir=temporary, run_id='same', password_file='not-read')
                seed.assert_not_called()

    def test_legacy_assessment_cannot_overwrite_output_or_consume_protocol_fixture(self):
        self.module()
        import assessment_300
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / 'fixture.json'
            fixture = {'task_id': 3, 'run_id': 'run', 'usernames': [f'S{i}' for i in range(300)], 'password': 'private'}
            manifest.write_text(json.dumps(fixture))
            capacity = directory / 'capacity.json'
            capacity.write_text(json.dumps({'source': 'server_cgroup_and_deployment_environment'}))
            result = directory / 'result.json'
            result.write_text('OLD FAILURE')
            argv = ['assessment_300', '--base-url', 'http://localhost', '--manifest', str(manifest),
                    '--capacity-report', str(capacity), '--output', str(result)]
            with patch.object(sys, 'argv', argv), patch.object(assessment_300, 'run_phases') as phases:
                with self.assertRaises(FileExistsError):
                    assessment_300.main()
                phases.assert_not_called()
            self.assertEqual(result.read_text(), 'OLD FAILURE')
            fixture['capture_protocol'] = self.module().CONTRACT
            manifest.write_text(json.dumps(fixture))
            with patch.object(sys, 'argv', argv), patch.object(assessment_300, 'run_phases') as phases:
                with self.assertRaisesRegex(SystemExit, 'protocol'):
                    assessment_300.main()
                phases.assert_not_called()

    def test_history_protocol_preparation_uses_capture_runner_but_remains_bounded(self):
        module = self.module()
        import assessment_300
        from argparse import Namespace
        manifest = {'task_id': 3, 'course_id': 2, 'experiment_id': 4, 'capture_protocol': module.CONTRACT}
        args = Namespace(base_url='http://localhost', timeout=35, fixture_preparation=True)
        runner = assessment_300.fixture_runner(args, manifest)
        self.assertIsInstance(runner, module.CaptureProtocolMixin)
        self.assertEqual(runner.protocol_course_id, 2)
        self.assertEqual(runner.protocol_experiment_id, 4)
        args.fixture_preparation = False
        with self.assertRaisesRegex(SystemExit, 'protocol'):
            assessment_300.fixture_runner(args, manifest)

    def test_formal_acceptance_cannot_pass_without_capture_protocol_evidence(self):
        sys.path.insert(0, str(Path(settings.BASE_DIR) / 'scripts/load'))
        from school_mixed_300 import evaluate
        value = evaluate({}, {}, None)
        self.assertIn('both_groups_real_capture_protocol', value['checks'])
        self.assertFalse(value['checks']['both_groups_real_capture_protocol'])

    def test_protocol_fixture_specs_cycle_five_types_and_enable_frozen_tools(self):
        from apps.experiments.management.commands.seed_assessment_load import protocol_question_spec, protocol_tools
        rows = [protocol_question_spec(index) for index in range(20)]
        self.assertEqual({row['question_type'] for row in rows}, set(self.module().QUESTION_TYPES))
        self.assertEqual([row['question_type'] for row in rows[:5]], list(self.module().QUESTION_TYPES))
        self.assertGreaterEqual(len(rows[2]['correct_answer']), 2)
        tools = protocol_tools([{'code': f'LOAD-Q{index + 1:02d}', **row} for index, row in enumerate(rows)])
        from apps.experiments.research_tool_config import validate_research_tools
        self.assertTrue(validate_research_tools(tools)['doubt_enabled'])
        self.assertEqual(sum(rule['required'] for rule in tools['rules']), 4)
        self.assertEqual(len(tools['hints']), 4)

    def test_protocol_workflow_uses_leases_targets_replay_and_post_terminal_feedback(self):
        module = self.module()
        from assessment_300 import Runner, StudentState
        class Harness(module.CaptureProtocolMixin, Runner):
            protocol_course_id = 2
            protocol_experiment_id = 3
        runner = Harness('http://localhost', 3, 35)
        state = StudentState('PRIVATE-STUDENT', 'PRIVATE-PASSWORD')
        state.session_id, state.assignment_ids = 11, [1]
        calls, saved, ingest = [], {}, {}
        versions = {}
        from apps.assessment.event_schema_registry import validate_event_payload

        def request(_, path, *, method='GET', body=None):
            calls.append((path, body))
            if '/learning/' in path:
                return 200, {'feedback_capture': {'session_id': 11, 'attempt_id': 21, 'event_batch_url': '/events/',
                    'authorized': {'correctness': True, 'correct_answer': True, 'explanation': True}, 'mode': 'full',
                    'answer_event_id': saved[21]['target_event_id'], 'snapshot_id': None}}, .001
            if '/questions/' in path:
                index = int(path.strip('/').split('/')[-1])
                attempt = 20 + index
                kind = module.QUESTION_TYPES[index - 1]
                return 200, {'assignment': {'attempt_id': attempt}, 'question': {'question_type': kind,
                    'snapshot_id': index, 'options': [{'key': 'A'}, {'key': 'B'}]},
                    'initial_response_state': saved.get(attempt, {}).get('answer', [] if kind == 'multiple_choice' else ('' if kind in ('fill_blank', 'short_answer') else None)),
                    'navigation': [{'assignment_id': number} for number in range(1, 6)],
                    'actions': {'save_draft_url': f'/save/{attempt}/', 'submit_url': f'/submit/{attempt}/',
                        'research_tools_url': f'/tools/{attempt}/', 'finish_url': '/finish/'},
                    'process': {'capture_version': 'capture-v2', 'event_batch_url': '/events/',
                        'active_response_event_id': saved.get(attempt, {}).get('target_event_id'), 'response_version': versions.get(attempt, 0),
                        'writer_lease_acquire_url': '/acquire/', 'writer_lease_release_url': '/release/'}}, .001
            if path == '/events/':
                acks = []
                for event in body['events']:
                    validate_event_payload(event['event_type'], 1, event['payload'])
                    number = ingest.setdefault(event['event_id'], len(ingest) + 1)
                    acks.append({'event_id': event['event_id'], 'client_sequence_no': event['client_sequence_no'], 'server_ingest_sequence_no': number})
                return 200, {'success': True, 'acknowledgements': acks, 'rejections': []}, .001
            if path == '/acquire/':
                return 200, {'success': True, 'lease_token': 'lease', 'client_instance_id': body['client_instance_id']}, .001
            if path == '/finish/':
                return 200, b'<html>results</html>', .001
            if path.startswith('/save/'):
                attempt = int(path.strip('/').split('/')[-1])
                self.assertEqual(body['expected_version'], versions.get(attempt, 0))
                self.assertEqual(body['writer_lease_token'], 'lease')
                saved[attempt] = body
                versions[attempt] = versions.get(attempt, 0) + 1
                return 200, {'success': True, 'active_response_event_id': body['target_event_id'], 'response_version': versions[attempt]}, .001
            if path.startswith('/submit/'):
                self.assertEqual(body['writer_lease_token'], 'lease')
                return 200, {'success': True}, .001
            if path.startswith('/tools/'):
                if not body:
                    return 200, {'success': True, 'content_version': 'a' * 64, 'doubt_enabled': True, 'check_enabled': True,
                        'hints': [{'id': 'hint'}], 'rules': [{'tool': 'check', 'required': True, 'id': 'rule'}]}, .001
                return 200, {'success': True, 'event_id': body['operation_id'], 'content_version': 'a' * 64}, .001
            return 200, {'success': True}, .001

        with patch.object(Runner, 'request', side_effect=request):
            runner.answer(state)
        summary = runner.protocol_summary()
        self.assertEqual(summary['completed_sessions'], 1)
        self.assertTrue(summary['all_events_acknowledged'])
        self.assertGreater(summary['emissions'], summary['unique_events'])
        self.assertEqual(summary['emissions'], summary['acknowledgements'])
        self.assertFalse(summary['browser_dom_ime_offline_verified'])
        self.assertTrue(all(summary['scenarios'].get(kind) == 1 for kind in module.QUESTION_TYPES))
        self.assertEqual(runner.ledger['11']['answers']['25']['state_sha256'], module.digest({'question_type': 'short_answer', 'value': '离线刷新后'}))
        self.assertNotIn('PRIVATE', json.dumps(runner.ledger))
        self.assertNotIn('离线刷新后', json.dumps(runner.ledger, ensure_ascii=False))
        finish_index = next(index for index, (path, _) in enumerate(calls) if path == '/finish/')
        feedback_index = next(index for index, (path, _) in enumerate(calls) if '/learning/' in path)
        self.assertGreater(feedback_index, finish_index)
