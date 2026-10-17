"""Independent reconciliation retains planned failures and checks a read-only snapshot."""
import copy
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, transaction
from django.test import SimpleTestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import test_session_submission as submission_fixtures


def inputs():
    accounts, roster = [], []
    for number, role in enumerate(('save', 'submission', 'submission'), start=1):
        questions = [{'assignment_id': number * 10, 'attempt_id': number * 100, 'question_type': 'single_choice'}]
        accounts.append({'account_id': str(number), 'user_id': number, 'taskId': number, 'courseId': number,
                         'session_id': number, 'questions': questions})
        roster.append({'account_id': str(number), 'user_id': number, 'task_id': number, 'course_id': number,
                       'session_id': number, 'role': role, 'answers': [{**questions[0], 'value': 'A', 'confidence_probability': None}],
                       'save_schedule': [{'assignment_id': number * 10, 'value': 'A'}]})
    fixture = {'accounts': accounts, 'isolated_demo': True, 'database': 'kt_e2e', 'run_id': 'test-run',
               'source_ownership': {'command': 'seed_prepared_startup_load'}, 'fixture_spec': {}, 'plan_sha': ''}
    plan = {'version': 'student-capacity-protocol-v1', 'count': 3, 'mode': 'submission-mixed', 'run_id': 'test-run',
            'plan_id': 'frozen-plan', 'roster': roster}
    return plan, fixture


def freeze_v3_fixture(fixture):
    from .management.commands.verify_student_capacity_load import digest
    fixture['fixture_spec'] = {'content_binding_sha256': digest(fixture['accounts'])}
    fixture['fixture_id'] = fixture['plan_sha'] = digest(fixture['fixture_spec'])
    return fixture


class CapacityReconciliationInputTests(SimpleTestCase):
    def test_confidence_payload_allows_only_the_server_derived_answer_snapshot(self):
        from .capacity_reconciliation import payload_issues
        raw = {'event_type': 'confidence_observed', 'event_version': 1,
               'payload': {'answer_event_id': 'head', 'probability': 0, 'stage': 'responded'}}
        stored = {**raw, 'attempt_id': 1, 'payload': {**raw['payload'], 'snapshot_id': 'answer-snapshot'}}
        snapshot = {'events': {'head': {'attempt_id': 1, 'event_type': 'response_transition',
                                      'payload': {'after_snapshot_id': 'answer-snapshot'}}}}
        for version in (1, 2):
            with self.subTest(event_version=version):
                self.assertEqual(payload_issues({**raw, 'event_version': version},
                                               {**stored, 'event_version': version}, snapshot), [])
        presented = {**raw, 'payload': {'answer_event_id': None, 'probability': None, 'stage': 'presented'}}
        self.assertEqual(payload_issues(presented, {**stored, 'payload': {**presented['payload'], 'snapshot_id': None}}, snapshot), [])
        for change in ({'snapshot_id': 'other'}, {'probability': None}, {'unexpected': True}):
            with self.subTest(change=change):
                self.assertTrue(payload_issues(raw, {**stored, 'payload': {**stored['payload'], **change}}, snapshot))
        foreign = copy.deepcopy(snapshot)
        foreign['events']['head']['attempt_id'] = 2
        self.assertTrue(payload_issues(raw, stored, foreign))
        self.assertTrue(payload_issues(raw, stored, {'events': {}}))

    def test_v3_accepts_independent_fixture_identity_and_superseded_raw_action(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        plan.update(version='student-capacity-protocol-v3', source_fixture_id='source', baseline_snapshot_id='snapshot', run_id='cell')
        fixture.update(source_fixture_id='source', baseline_snapshot_id='snapshot')
        fixture['source_ownership']['command'] = 'prepare_student_capacity_baseline'
        planned = plan['roster'][0]
        fixture['accounts'][0]['questions'][0]['confidence_condition'] = 'prompt'
        planned['answers'][0].update(confidence_condition='prompt', confidence_probability=0)
        planned['save_schedule'] = [{'assignment_id': 10, 'value': 'B'}, {'assignment_id': 10, 'value': 'A'}]
        record = {**planned, 'plan_id': plan['plan_id'], 'run_id': 'cell', 'status': 'completed',
            'save_samples': [{'schedule_index': i, 'assignment_id': 10, 'event_id': 'event' + str(i),
                             'status': 'superseded' if i == 0 else 'acknowledged'} for i in range(2)],
            'expected_attempts': [{'assignment_id': 10, 'attempt_id': 100, 'answer': 'A',
                'target_event_id': 'event1', 'response_version': 1, 'confidence_probability': 0}]}
        rows, unknown = adapt_inputs(plan, freeze_v3_fixture(fixture), [record])
        self.assertEqual(rows[0]['input_issues'], [])
        self.assertEqual(rows[0]['confidence'], {10: 0})
        self.assertEqual(unknown, 0)

    def test_v3_confidence_checks_zero_and_binding_without_weakening_old_rules(self):
        from .capacity_reconciliation import confidence_issues
        expected = {'probability': 0, 'target_event_id': 'head', 'terminal': True, 'condition': 'prompt'}
        observed = {'probability': 0, 'draft_probability': 0, 'draft_answer_event_id': 'head',
                    'answer_event_id': 'head', 'response_status': 'committed'}
        self.assertEqual(confidence_issues(expected, observed), [])
        observed.update(probability=None, draft_answer_event_id='old')
        self.assertIn('confidence_probability_differs', confidence_issues(expected, observed))
        self.assertIn('confidence_answer_binding_differs', confidence_issues(expected, observed))

    def test_v3_rejects_reused_raw_head_and_missing_browser_driver_identity(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        plan.update(version='student-capacity-protocol-v3', source_fixture_id='source', baseline_snapshot_id='snapshot')
        fixture.update(source_fixture_id='source', baseline_snapshot_id='snapshot')
        fixture['source_ownership']['command'] = 'prepare_student_capacity_baseline'
        planned = plan['roster'][0]
        planned.update(driver='browser', save_schedule=[{'assignment_id': 10, 'value': 'A'}] * 2)
        record = {**planned, 'driver': 'protocol', 'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'status': 'completed',
            'save_samples': [{'schedule_index': i, 'assignment_id': 10, 'event_id': 'reused',
                             'status': 'acknowledged'} for i in range(2)]}
        rows, _ = adapt_inputs(plan, freeze_v3_fixture(fixture), [record])
        self.assertIn('protocol_planned_action_event_reused', rows[0]['input_issues'])
        self.assertIn('protocol_record_driver_differs', rows[0]['input_issues'])

    def test_v3_changed_full_account_binding_is_rejected_even_outside_selected_roster(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        plan.update(version='student-capacity-protocol-v3', source_fixture_id='source', baseline_snapshot_id='snapshot')
        fixture.update(source_fixture_id='source', baseline_snapshot_id='snapshot')
        fixture['source_ownership']['command'] = 'prepare_student_capacity_baseline'
        freeze_v3_fixture(fixture)
        fixture['accounts'].append({'account_id': 'unexpected-account'})
        with self.assertRaisesRegex(ValueError, 'binding'):
            adapt_inputs(plan, fixture, [])

    def test_missing_and_failed_records_remain_in_the_planned_denominator(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        record = {**plan['roster'][0], 'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'status': 'failed'}
        rows, unknown = adapt_inputs(plan, fixture, [record])
        self.assertEqual(len(rows), 3)
        self.assertEqual([row['witness_state'] for row in rows], ['failed', 'missing', 'missing'])
        self.assertEqual([row['terminal_required'] for row in rows], [False, True, True])
        self.assertEqual([row['answers'] for row in rows], [{10: 'A'}, {20: 'A'}, {30: 'A'}])
        self.assertEqual(unknown, 0)

    def test_observed_answers_cannot_replace_the_frozen_final_answers(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        record = {**plan['roster'][1], 'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'status': 'completed',
                  'expected_attempts': [{'assignment_id': 20, 'attempt_id': 200, 'answer': 'B', 'response_version': 1,
                                         'target_event_id': 'different'}]}
        rows, _ = adapt_inputs(plan, fixture, [record])
        self.assertEqual(rows[1]['answers'], {20: 'A'})
        self.assertIn('protocol_final_answer_differs', rows[1]['input_issues'])

    def test_complete_record_compares_every_planned_save_without_relabeling_acknowledgements(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        planned = plan['roster'][1]
        record = {**planned, 'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'status': 'completed',
                  'final_status': 'completed', 'save_samples': [{'schedule_index': 0, 'assignment_id': 20, 'status': 'acknowledged'}],
                  'expected_attempts': [{'assignment_id': 20, 'attempt_id': 200, 'answer': 'A', 'response_version': 1,
                                         'target_event_id': 'head', 'confidence_probability': None}]}
        rows, _ = adapt_inputs(plan, fixture, [record])
        self.assertEqual(rows[1]['input_issues'], [])

    def test_duplicate_and_unknown_records_do_not_disappear(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        record = {**plan['roster'][0], 'plan_id': plan['plan_id'], 'run_id': plan['run_id'], 'status': 'completed'}
        rows, unknown = adapt_inputs(plan, fixture, [record, copy.deepcopy(record), {'account_id': 'unknown'}])
        self.assertEqual(rows[0]['witness_state'], 'duplicate')
        self.assertEqual(unknown, 1)
        self.assertEqual(len(rows), plan['count'])

    def test_inconsistent_plan_or_fixture_binding_is_rejected(self):
        from .capacity_reconciliation import adapt_inputs
        plan, fixture = inputs()
        fixture['accounts'][0]['user_id'] = 999
        with self.assertRaisesRegex(ValueError, 'ownership'):
            adapt_inputs(plan, fixture, [])
        fixture['accounts'][0]['user_id'] = 1
        plan['count'] = 2
        with self.assertRaisesRegex(ValueError, 'denominator'):
            adapt_inputs(plan, fixture, [])

    def test_projection_readiness_requires_fresh_revision_coverage_and_rows(self):
        from .capacity_reconciliation import projection_issues
        snapshot = {'source_revision': 7, 'projection_version': 'current', 'status': 'succeeded', 'is_current': True,
                    'input_status': 'complete', 'coverage_exists': True, 'finished_at': timezone.now(),
                    'ready_after': timezone.now() - timedelta(seconds=100), 'event_high_watermark': 12,
                    'expected_event_high_watermark': 12, 'response_rows_match': True,
                    'visit_rows_match': True, 'behavior_rows_match': True, 'export_readable': True}
        self.assertEqual(projection_issues(snapshot, revision=7, projection_version='current'), [])
        snapshot.update(source_revision=6, input_status='partial', behavior_rows_match=False)
        issues = projection_issues(snapshot, revision=7, projection_version='current')
        self.assertIn('statistics_source_revision_stale', issues)
        self.assertIn('statistics_input_not_complete', issues)
        self.assertIn('statistics_behavior_rows_differ', issues)


class CapacityReconciliationSafetyTests(SimpleTestCase):
    def stop(self, state, issues=(), statistics=(), unknown=0):
        from .capacity_reconciliation import _requires_safety_stop
        return _requires_safety_stop([{'witness_state': state}], [{'issues': list(issues)}], statistics, unknown)

    def test_any_domain_or_material_difference_in_a_completed_witness_stops(self):
        for code in ('capture_sequence_gap_or_duplicate', 'protocol_final_attempt_missing',
                     'protocol_expected_attempts_incomplete', 'protocol_evidence_invalid'):
            with self.subTest(code=code):
                self.assertTrue(self.stop('completed', [code]))

    def test_failed_workload_stops_for_any_confirmed_material_mismatch(self):
        for code in ('confirmed_checkpoint_content_differs', 'confirmed_event_envelope_differs',
                     'confirmed_transition_parent_differs', 'protocol_database_client_events_not_all_witnessed',
                     'protocol_time_sync_missing_or_differs', 'protocol_event_time_sync_differs'):
            with self.subTest(code=code):
                self.assertTrue(self.stop('failed', [code]))

    def test_incomplete_workload_alone_is_not_a_data_loss_stop(self):
        for state in ('missing', 'failed'):
            self.assertFalse(self.stop(state, ['witness_' + state, 'saved_answer_differs', 'activation_missing_start_time']))
        self.assertFalse(self.stop('completed'))

    def test_unbounded_record_identity_stops_even_without_confirmed_material_loss(self):
        self.assertTrue(self.stop('duplicate', ['witness_duplicate']))
        self.assertTrue(self.stop('binding_mismatch', ['witness_binding_mismatch']))
        self.assertTrue(self.stop('completed', unknown=1))

    def test_fresh_projection_row_contradictions_stop_separately_from_stats_delay(self):
        self.assertTrue(self.stop('completed', statistics=[{'issues': ['statistics_behavior_rows_differ',
            'statistics_input_not_complete']}]))
        self.assertTrue(self.stop('failed', statistics=[{'issues': ['statistics_row_counts_differ']}]))
        self.assertFalse(self.stop('completed', statistics=[{'issues': ['statistics_current_missing',
            'statistics_not_ready_within_300_seconds']}]))
        self.assertFalse(self.stop('completed', statistics=[{'issues': ['statistics_response_rows_differ',
            'statistics_source_revision_stale']}]))

    def test_emitted_but_uncommitted_events_are_not_misreported_as_unexpected_database_writes(self):
        from .capacity_reconciliation import _additional_evidence
        expected = {'witness': {'client_instance_id': 'client', 'capture_events': [{'event_id': 'pending'}]}}
        self.assertEqual(_additional_evidence(expected, {'events': {}}), [])


@skipUnless(connection.vendor == 'postgresql', 'Read-only isolation requires PostgreSQL')
class CapacityReconciliationDatabaseTests(TransactionTestCase):
    setUp = submission_fixtures.SessionSubmissionTests.setUp

    def test_snapshot_enforces_repeatable_read_read_only_and_keeps_missing_students(self):
        from .capacity_reconciliation import reconcile_capacity
        self.student.set_password('Isolated-test-password!')
        self.student.save(update_fields=['password'])
        questions = list(self.session.attempts.values('id', 'assignment_id'))
        account = {'account_id': 'owned', 'user_id': self.student.pk, 'taskId': None, 'courseId': None,
                   'session_id': self.session.pk, 'questions': [{'attempt_id': a['id'], 'assignment_id': a['assignment_id']} for a in questions]}
        fixture = {'accounts': [account], 'isolated_demo': True, 'database': 'kt_e2e', 'run_id': 'test-run',
                   'source_ownership': {'command': 'seed_prepared_startup_load'}}
        plan = {'version': 'student-capacity-protocol-v1', 'mode': 'login', 'count': 1, 'run_id': 'test-run',
                'plan_id': 'id', 'roster': [{**account, 'task_id': None, 'course_id': None, 'role': 'login',
                                           'answers': [], 'save_schedule': []}]}
        evidence = io.StringIO()
        with CaptureQueriesContext(connection) as queries:
            report = reconcile_capacity(plan, fixture, [], require_statistics=False, evidence_output=evidence)
        self.assertTrue(any('REPEATABLE READ, READ ONLY' in row['sql'] for row in queries))
        self.assertFalse(any(row['sql'].lstrip().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for row in queries))
        self.assertEqual(report['planned'], 1)
        self.assertEqual(report['clean'], 0)
        self.assertEqual(report['witness_counts'], {'missing': 1})
        self.assertFalse(report['integrity_verified'])
        self.assertEqual(report['capacity_verdict'], None)
        independently_saved = [json.loads(line) for line in evidence.getvalue().splitlines()]
        self.assertEqual([row['session_id'] for row in independently_saved], [self.session.pk])
        self.assertEqual(independently_saved[0]['plan_id'], 'id')
        self.assertIn('response_snapshots', independently_saved[0]['database'])
        self.assertTrue(all('writer_lease_token' not in a for a in independently_saved[0]['database']['attempts'].values()))

    def test_completed_session_without_projection_is_visible_as_not_ready(self):
        from .capacity_reconciliation import statistics_snapshot, projection_issues
        from apps.analytics.research_materialization import PROJECTION_VERSION
        snapshot = statistics_snapshot(self.session)
        self.assertEqual(snapshot['status'], 'missing')
        issues = projection_issues(snapshot, revision=self.session.research_source_revision, projection_version=PROJECTION_VERSION)
        self.assertIn('statistics_current_missing', issues)
        self.assertIn('statistics_input_not_complete', issues)

    def test_command_binds_input_files_and_writes_a_reviewable_report(self):
        from django.core.management import call_command
        from .management.commands.verify_student_capacity_load import digest
        plan, fixture = inputs()
        fixture['plan_sha'] = digest(fixture['fixture_spec'])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / 'fixture.json'
            fixture_path.write_text(json.dumps(fixture), encoding='utf-8')
            plan.update(fixture_spec_sha256=fixture['plan_sha'], fixture_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest())
            plan.pop('plan_id')
            plan['plan_id'] = digest(plan)
            (root / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
            (root / 'records.jsonl').write_text('', encoding='utf-8')
            output = io.StringIO()
            call_command('verify_student_capacity_load', fixture=str(fixture_path), plan=str(root / 'plan.json'),
                records=str(root / 'records.jsonl'), output=str(root / 'verified.json'), skip_statistics=True, stdout=output)
            report = json.loads((root / 'verified.json').read_text(encoding='utf-8'))
            self.assertEqual(report['planned'], 3)
            self.assertFalse(report['integrity_passed'])
            self.assertEqual(report['witness_counts'], {'missing': 3})
            self.assertEqual(json.loads(output.getvalue())['planned'], 3)
            self.assertIn('capacity_reconciliation.py', report['source_sha256'])

    def test_nonisolated_database_name_is_rejected_before_any_query(self):
        from .capacity_reconciliation import reconcile_capacity
        plan, fixture = inputs()
        with patch.dict(connection.settings_dict, {'NAME': 'kt_platform'}):
            with self.assertNumQueries(0), self.assertRaisesRegex(ValueError, 'isolated'):
                reconcile_capacity(plan, fixture, [])


@skipUnless(connection.vendor == 'postgresql', 'Source-row reconciliation requires PostgreSQL')
class CapacityStatisticsSourceTests(TransactionTestCase):
    def setUp(self):
        from apps.analytics import test_teacher_data_center_api as fixtures
        fixtures.TeacherDataCenterApiTests.setUp(self)
        now = timezone.now()
        type(self.session).objects.filter(pk=self.session.pk).update(completed_at=now,
            authoritative_terminal_at=now, research_source_changed_at=now)
        self.session.refresh_from_db()

    def test_source_export_rows_equal_the_published_projection_inside_readonly_transaction(self):
        from .capacity_reconciliation import statistics_snapshot
        from apps.analytics.research_materialization import (enqueue_session_materialization,
            claim_session_materialization, run_session_materialization)
        enqueue_session_materialization(self.session)
        job = claim_session_materialization(worker_id='capacity-source-test', lease_seconds=60)
        result = run_session_materialization(job, worker_id='capacity-source-test')
        self.assertEqual(result.status, 'succeeded', result.error)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            snapshot = statistics_snapshot(self.session)
        self.assertTrue(snapshot['export_readable'])
        self.assertTrue(snapshot['response_rows_match'])
        self.assertTrue(snapshot['visit_rows_match'])
        self.assertTrue(snapshot['behavior_rows_match'])
        self.assertTrue(snapshot['row_counts_match'])
