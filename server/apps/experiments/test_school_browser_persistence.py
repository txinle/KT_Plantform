"""Read-only verifier checks use model-shaped rows; no database is opened."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid
from contextlib import ExitStack
from unittest.mock import patch
import json

from django.test import SimpleTestCase

from apps.experiments.management.commands import verify_school_protocol_load as verifier
from scripts.load.capture_protocol import BROWSER_ALIGNED_WORKLOAD, BrowserAlignedStream, digest


class BrowserPersistenceChecksTests(SimpleTestCase):
    def test_navigation_receipt_verifier_distinguishes_confirmed_unknown_and_conflicting_material(self):
        receipt = {'committed': True, 'navigation_token': 'token', 'to_assignment_id': 12,
                   'active_response_event_id': 'head', 'writer_lease_released': True}
        expected = {'attempt_id': 11, 'material_fingerprint': 'fingerprint', 'to_assignment_id': 12,
                    'target_event_id': 'head', 'acknowledged': True, 'receipt_sha256': digest(receipt)}
        ledger = {'sessions': {'7': {'navigation_receipts': {'token': expected}}}}
        stored = SimpleNamespace(token='token', attempt_id=11, material_fingerprint='fingerprint', receipt=receipt)
        checks = verifier.navigation_receipt_checks(ledger, [stored])
        self.assertTrue(checks['known_navigation_integrity_passed'])
        self.assertTrue(checks['all_navigation_receipts_confirmed_and_matched'])
        changed = deepcopy(stored)
        changed.material_fingerprint = 'changed'
        self.assertFalse(verifier.navigation_receipt_checks(ledger, [changed])['known_navigation_integrity_passed'])
        expected['acknowledged'] = False
        unknown = verifier.navigation_receipt_checks(ledger, [])
        self.assertTrue(unknown['known_navigation_integrity_passed'])
        self.assertFalse(unknown['all_navigation_receipts_confirmed_and_matched'])

    def persisted_workflow(self):
        from apps.experiments.test_school_browser_aligned import SchoolBrowserAlignedTests
        runner, _, calls = SchoolBrowserAlignedTests().workflow()
        private = deepcopy(runner.ledger)
        ledger = {'workload_version': BROWSER_ALIGNED_WORKLOAD, 'sessions': private}
        clock_fields = ('client_wall_time_utc', 'client_time_origin', 'client_received_wall_time_utc')

        def stored_document(document, id_field):
            values = deepcopy(document)
            values['pk'] = values.pop(id_field)
            for field in clock_fields:
                if field in values:
                    values[field] = datetime.fromisoformat(values[field])
            for field in ('client_monotonic_ms', 'client_received_monotonic_ms'):
                if field in values:
                    values[field] = Decimal(str(values[field]))
            return SimpleNamespace(**values)

        events = {}
        for _, _, body in calls:
            for document in body.get('events', []) if body else []:
                event = stored_document(document, 'event_id')
                event.student_id = 7
                event.client_instance_id = private['11']['events'][event.pk]['client_instance_id']
                event.server_ingest_sequence_no = 1
                event.time_sync_id = document['time_sync_id']
                events[event.pk] = event
                if event.event_type == 'response_transition':
                    event.payload['after_snapshot_id'] = str(uuid.uuid4())
        for event in list(events.values()):
            if event.event_type in ('research_tool_requested', 'research_tool_observed', 'confidence_observed'):
                target = events.get(event.payload.get('answer_event_id'))
                event.payload['snapshot_id'] = target.payload['after_snapshot_id'] if target else None
        for path, _, body in calls:
            if path.startswith('/tools/') and body:
                events[body['operation_id']] = SimpleNamespace(pk=body['operation_id'], event_type='research_tool',
                    session_id=11, student_id=7, attempt_id=int(path.strip('/').split('/')[-1]),
                    client_instance_id=None, payload={**body, 'content_version': 'a' * 64})
        checkpoints = []
        for value in private['11']['checkpoints'].values():
            row = stored_document(value['document'], 'checkpoint_id')
            row.student_id = 7
            checkpoints.append(row)
        syncs = []
        for sample_id, value in private['11']['time_syncs'].items():
            sample = {**value['create'], **value['finalize']}
            row = stored_document(sample, 'time_sync_id')
            row.sync_quality = 'valid'
            row.finalized_at = datetime.now(timezone.utc)
            row.estimated_clock_offset_ms, row.estimated_rtt_ms = 0, 1
            syncs.append(row)
        visits, per_attempt = [], {}
        for index, value in enumerate(private['11']['visits'], 1):
            attempt = value['attempt_id']
            per_attempt[attempt] = per_attempt.get(attempt, 0) + 1
            value.update(visit_id=index, visit_index=per_attempt[attempt], entry_event_id=str(uuid.uuid4()),
                         navigation_id=value['entry_material']['navigation_id'])
            value['entry_receipt'].update({key: value[key] for key in ('visit_id', 'visit_index', 'entry_event_id', 'navigation_id')})
            visits.append(SimpleNamespace(pk=index, session_id=11, attempt_id=attempt,
                          visit_index=per_attempt[attempt], event_sequence=index))
            events[value['entry_event_id']] = SimpleNamespace(pk=value['entry_event_id'], session_id=11,
                student_id=7, attempt_id=attempt, assignment_id=value['assignment_id'], event_type='item_entered',
                client_instance_id=None, correlation_id=value['navigation_id'],
                client_wall_time_utc=datetime.fromisoformat(value['entry_material']['client_occurred_at']),
                client_monotonic_ms=Decimal(str(value['entry_material']['client_monotonic_ms'])),
                client_time_origin=datetime.fromisoformat(value['entry_material']['client_time_origin']),
                payload={'visit_id': index, 'entry_reason': value['entry_material']['reason']})
        config = {'rules': [], 'hints': []}
        session = SimpleNamespace(pk=11, student_id=7, first_open_count=20, submission_count=20,
                                  visit_event_count=58, status='completed', terminal_at=datetime.now(timezone.utc),
                                  student=SimpleNamespace(username='PRIVATE-STUDENT'),
                                  version=SimpleNamespace(configuration={'research_tools': config}))
        attempts, submissions, confidence = [], [], []
        for attempt_id, value in private['11']['answers'].items():
            attempt_id = int(attempt_id)
            snapshot_id = events[value['target_event_id']].payload['after_snapshot_id']
            snapshot = SimpleNamespace(code=str(attempt_id), question_type=value['question_type'])
            attempt = SimpleNamespace(pk=attempt_id, session_id=11, session=session,
                assignment=SimpleNamespace(assigned_sequence_index=attempt_id - 20, snapshot=snapshot),
                final_response_event_id=value['target_event_id'], submission_token=value['submission_token'],
                final_response_snapshot_id=snapshot_id,
                final_answer=events[value['target_event_id']].payload['after_state']['value'],
                active_response_event_id=value['target_event_id'], response_version=value['response_version'],
                submitted_at=datetime.now(timezone.utc), status='submitted')
            attempts.append(attempt)
            submissions.append(SimpleNamespace(attempt_id=attempt_id, submission_token=value['submission_token'],
                               final_response_snapshot_id=snapshot_id, final_answer=attempt.final_answer))
            key = str(uuid.uuid5(uuid.NAMESPACE_OID, str(attempt_id)))
            confidence.append(SimpleNamespace(pk=key, attempt_id=attempt_id, condition='prompt', response_status='skipped',
                              prompt_shown_at=datetime.now(timezone.utc), probability=None))
            for event_type in ('confidence_prompt_shown', 'confidence_skipped'):
                token = str(uuid.uuid4())
                events[token] = SimpleNamespace(pk=token, event_type=event_type, session_id=11, student_id=7,
                    attempt_id=attempt_id, client_instance_id=None, payload={'confidence_assignment_id': key})
            if value['question_type'] == 'single_choice':
                config['rules'].append({'id': 'rule', 'tool': 'check', 'required': True, 'question_code': str(attempt_id)})
            if value['question_type'] == 'fill_blank':
                config['hints'].append({'id': 'hint', 'question_code': str(attempt_id), 'contains_answer': False})
        events = list(events.values())
        for index, row in enumerate(events, 1):
            row.server_ingest_sequence_no = index
            if row.client_instance_id is not None:
                private['11']['events'][row.pk]['server_ingest_sequence_no'] = index
        session.next_ingest_sequence_no = len(events) + 1
        return [{'student_count': 1, 'item_count': 20}, ledger, [session], events, attempts,
                checkpoints, syncs, visits, submissions, confidence]

    def test_command_branch_queries_persisted_rows_and_outputs_only_aggregates(self):
        arguments = self.persisted_workflow()
        fixture, ledger = arguments[:2]
        fixture.update(task_id=3, usernames=['PRIVATE-STUDENT'])
        ledger['incomplete'] = False

        class Rows(list):
            def select_related(self, *args):
                return self

            def count(self):
                return len(self)

        sources = [(verifier.ExperimentSession, arguments[2]), (verifier.AssessmentEvent, arguments[3]),
            (verifier.Attempt, arguments[4]), (verifier.ClientCaptureCheckpoint, arguments[5]),
            (verifier.ClientTimeSync, arguments[6]), (verifier.QuestionVisitEvent, arguments[7]),
            (verifier.SubmissionEvent, arguments[8]), (verifier.ConfidenceAssignment, arguments[9]),
            (verifier.NavigationReceipt, [])]
        with ExitStack() as stack:
            mocks = {}
            for model, rows in sources:
                def query(*args, rows=rows, **kwargs):
                    return Rows(row for row in rows if 'client_instance_id__isnull' not in kwargs or row.client_instance_id is not None)
                mocks[model] = stack.enter_context(patch.object(model.objects, 'filter', side_effect=query))
            result = verifier.Command().verify(fixture, ledger, [], BROWSER_ALIGNED_WORKLOAD)
            self.assertTrue(result['passed'], result['checks'])
            self.assertTrue(result['known_fact_integrity_passed'], result['known_fact_integrity_checks'])
            self.assertEqual((result['visit_count'], result['time_sync_count'], result['answer_count']), (58, 1, 20))
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertNotIn('浏览器探针作答', json.dumps(result, ensure_ascii=False))
            for model in (verifier.ClientCaptureCheckpoint, verifier.ClientTimeSync, verifier.ConfidenceAssignment):
                mocks[model].reset_mock()
            legacy = verifier.Command().verify(fixture, ledger, [])
            self.assertTrue(legacy['passed'])
            self.assertNotIn('workload_version', legacy)
            self.assertNotIn('known_fact_integrity_passed', legacy)
            self.assertFalse(any(key.startswith('browser_') for key in legacy['checks']))
            for model in (verifier.ClientCaptureCheckpoint, verifier.ClientTimeSync, verifier.ConfidenceAssignment):
                mocks[model].assert_not_called()
            ledger['incomplete'] = True
            arguments[2][0].status = 'in_progress'
            arguments[2][0].terminal_at = None
            partial = verifier.Command().verify(fixture, ledger, [], BROWSER_ALIGNED_WORKLOAD)
            self.assertFalse(partial['passed'])
            self.assertFalse(partial['full_workflow_passed'])
            self.assertFalse(partial['checks']['all_terminal'])
            self.assertFalse(partial['checks']['ledger_complete'])
            self.assertTrue(partial['known_fact_integrity_passed'])

    def test_partial_known_facts_allow_unacknowledged_uncertainty_but_reject_material_conflicts(self):
        arguments = self.persisted_workflow()
        fixture, ledger, sessions, events, _, checkpoints = arguments[:6]
        fixture['usernames'] = ['PRIVATE-STUDENT']
        ledger['incomplete'] = True
        client = next(row for row in events if row.client_instance_id is not None)
        expected = ledger['sessions']['11']['events'][client.pk]
        expected.update(acknowledged=False, ack_count=0, server_ingest_sequence_no=None)
        report = verifier.known_fact_integrity(fixture, ledger, sessions, events, checkpoints)
        self.assertTrue(report['known_fact_integrity_passed'], report)
        self.assertEqual(report['unacknowledged_events_persisted_count'], 1)
        absent = [row for row in events if row.pk != client.pk]
        report = verifier.known_fact_integrity(fixture, ledger, sessions, absent, checkpoints)
        self.assertTrue(report['known_fact_integrity_passed'], report)
        self.assertEqual(report['unacknowledged_events_missing_count'], 1)
        client.payload['extra_unexpected_field'] = 'private'
        self.assertFalse(verifier.known_fact_integrity(fixture, ledger, sessions, events, checkpoints)['known_fact_integrity_passed'])
        client.payload.pop('extra_unexpected_field')
        acked = next(row for row in events if row.client_instance_id is not None and row.pk != client.pk)
        self.assertFalse(verifier.known_fact_integrity(fixture, ledger, sessions,
            [row for row in events if row.pk != acked.pk], checkpoints)['known_fact_integrity_passed'])
        checkpoint_id, value = next(iter(ledger['sessions']['11']['checkpoints'].items()))
        value['acknowledged'] = False
        without_checkpoint = [row for row in checkpoints if row.pk != checkpoint_id]
        report = verifier.known_fact_integrity(fixture, ledger, sessions, events, without_checkpoint)
        self.assertTrue(report['known_fact_integrity_passed'], report)
        self.assertEqual(report['unacknowledged_checkpoints_missing_count'], 1)
        checkpoints[0].pending_event_count += 1
        self.assertFalse(verifier.known_fact_integrity(fixture, ledger, sessions, events, checkpoints)['known_fact_integrity_passed'])

    def test_empty_ack_evidence_cannot_pass_known_fact_integrity(self):
        report = verifier.known_fact_integrity({'usernames': [], 'student_count': 1}, {'sessions': {}}, [], [], [])
        self.assertFalse(report['known_fact_integrity_passed'])

    def test_complete_persisted_browser_workflow_passes_and_corruptions_fail(self):
        arguments = self.persisted_workflow()
        checks = verifier.browser_persistence_checks(*arguments)
        self.assertTrue(all(checks.values()), checks)
        self.assertTrue(checks['browser_session_collector_with_continuous_sequence_and_page_clock'])
        for position in (3, 5, 6, 7, 8, 9):
            changed = deepcopy(arguments)
            changed[position].pop()
            self.assertFalse(all(verifier.browser_persistence_checks(*changed).values()), position)
        changed = deepcopy(arguments)
        changed[7][1].attempt_id = changed[7][0].attempt_id
        self.assertFalse(verifier.browser_persistence_checks(*changed)['browser_navigation_visit_order_and_render_counts'])
        changed = deepcopy(arguments)
        next(row for row in changed[3] if row.event_type == 'research_tool_observed').payload['server_event_id'] = 'missing'
        self.assertFalse(verifier.browser_persistence_checks(*changed)['browser_tool_requests_observations_and_server_references'])

    def checkpoint(self):
        document = BrowserAlignedStream(11).checkpoint('resume')
        values = deepcopy(document)
        values['pk'] = values.pop('checkpoint_id')
        values['student_id'] = 7
        for field in ('client_wall_time_utc', 'client_time_origin'):
            values[field] = datetime.fromisoformat(values[field])
        values['client_monotonic_ms'] = Decimal(str(values['client_monotonic_ms']))
        row = SimpleNamespace(**values)
        expected = {'document': document, 'payload_sha256': digest(document), 'acknowledged': True, 'emission_count': 1}
        return row, expected

    def test_checkpoint_matches_all_original_fields_and_hash_with_database_types(self):
        row, expected = self.checkpoint()
        self.assertTrue(verifier.checkpoint_matches(row, expected, 7))
        for field, value in [('pending_event_count', 1), ('student_id', 8), ('time_sync_id', 'other'),
                             ('quality_flags', ['changed']), ('client_monotonic_ms', Decimal('1.000'))]:
            altered = deepcopy(row)
            setattr(altered, field, value)
            self.assertFalse(verifier.checkpoint_matches(altered, expected, 7), field)
        expected['document']['failed_event_count'] = 1
        self.assertFalse(verifier.checkpoint_matches(row, expected, 7))

    def test_clock_sample_must_match_both_actual_http_documents_and_finalization(self):
        now = datetime.now(timezone.utc)
        create = {'session_id': 11, 'client_instance_id': 'client', 'client_wall_time_utc': now.isoformat(),
                  'client_monotonic_ms': 1.125, 'client_time_origin': now.isoformat()}
        finalize = {'time_sync_id': 'sample', 'client_instance_id': 'client',
                    'client_received_wall_time_utc': now.isoformat(), 'client_received_monotonic_ms': 3.125}
        row = SimpleNamespace(pk='sample', **{**create, 'client_wall_time_utc': now, 'client_time_origin': now},
                              client_received_wall_time_utc=now, client_received_monotonic_ms=Decimal('3.125'),
                              sync_quality='valid', finalized_at=now, estimated_rtt_ms=2, estimated_clock_offset_ms=0)
        expected = {'create': create, 'finalize': finalize, 'finalized': True}
        self.assertTrue(verifier.time_sync_matches(row, expected, 11))
        row.client_received_monotonic_ms = Decimal('4.125')
        self.assertFalse(verifier.time_sync_matches(row, expected, 11))
        row.client_received_monotonic_ms = Decimal('3.125')
        row.finalized_at = None
        self.assertFalse(verifier.time_sync_matches(row, expected, 11))

    def test_browser_branch_is_explicit_and_legacy_default_preserved(self):
        parser = verifier.Command().create_parser('manage.py', 'verify_school_protocol_load')
        self.assertEqual(parser.parse_args(['--manifest', 'm', '--output', 'o']).workload_version, verifier.CONTRACT)
        self.assertEqual(parser.parse_args(['--manifest', 'm', '--output', 'o', '--workload-version',
                                           BROWSER_ALIGNED_WORKLOAD]).workload_version, BROWSER_ALIGNED_WORKLOAD)

    def test_empty_database_evidence_cannot_pass_new_workload(self):
        checks = verifier.browser_persistence_checks({'student_count': 1, 'item_count': 20}, {'sessions': {}},
                                                    [], [], [], [], [], [], [], [])
        self.assertFalse(all(checks.values()))
