import copy
import hashlib
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.assessment.models import Attempt, ExperimentSession, GradeRecord, SubmissionEvent
from apps.assessment.response_snapshot_services import canonical_response_state, create_response_snapshot
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.analytics.models import RecordingCoverageRun, ResearchSessionMaterialization, ResearchSessionResponse, ResearchSessionBehavior
from apps.analytics.research_materialization import PROJECTION_VERSION, _scope_for_session
from apps.analytics.research_dataset import read_research_bundle
from scripts.load.capture_protocol import CONTRACT, digest


class SchoolArchitectureResultsTests(StudentApiFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.start().status_code, 201)
        self.session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.complete_session(self.session)
        self.attempt = self.session.attempts.select_related('assignment__snapshot').get()
        snapshot = create_response_snapshot(attempt=self.attempt, content='B')
        Attempt.objects.filter(pk=self.attempt.pk).update(final_response_snapshot=snapshot, score=1, is_correct=True)
        SubmissionEvent.objects.filter(attempt=self.attempt).update(final_response_snapshot=snapshot, score=1, is_correct=True)
        self.attempt.refresh_from_db()
        self.fixture = {'database': 'kt_e2e', 'run_id': 'private-fixture', 'source_config': 'frozen-config',
                        'capture_protocol': CONTRACT, 'course_id': self.course.pk, 'class_id': self.class_group.pk,
                        'experiment_id': self.experiment.pk, 'task_id': self.task.pk,
                        'student_count': 1, 'item_count': 1, 'usernames': [self.student.username]}
        submission = self.attempt.submission_event
        self.ledger = {'task_id': self.task.pk, 'source_config': 'frozen-config', 'capture_protocol': CONTRACT,
                       'student_count': 1, 'incomplete': False, 'sessions': {str(self.session.pk): {
                           'username_sha256': digest(self.student.username), 'complete': True, 'answers': {
                               str(self.attempt.pk): {'question_type': 'single_choice', 'target_event_id': str(self.attempt.final_response_event_id),
                                   'submission_token': str(submission.submission_token), 'response_version': self.attempt.response_version,
                                   'state_sha256': digest({'question_type': 'single_choice', 'value': canonical_response_state('single_choice', 'B')})}}}}}
        self.materialize()

    def materialize(self):
        self.session.refresh_from_db()
        bundle, _ = read_research_bundle(_scope_for_session(self.session), include_course_catalog=False)
        item = ResearchSessionMaterialization.objects.create(session=self.session, status='succeeded', is_current=True,
                  projection_version=PROJECTION_VERSION, source_revision=self.session.research_source_revision)
        for ordinal, row in enumerate(bundle['files']['responses'], 1):
            ResearchSessionResponse.objects.create(materialization=item, ordinal=ordinal, row=row)
        for ordinal, row in enumerate(bundle['files']['behavior_events'], 1):
            ResearchSessionBehavior.objects.create(materialization=item, ordinal=ordinal, row=row)
        return item

    def verify(self, fixture=None, ledger=None):
        from apps.experiments.management.commands.verify_school_architecture_results import Command
        return Command().verify(fixture or self.fixture, ledger or self.ledger)

    def test_real_grading_coverage_and_export_reconcile_without_persistent_writes(self):
        before = (list(Attempt.objects.values()), list(SubmissionEvent.objects.values()), list(GradeRecord.objects.values()),
                  RecordingCoverageRun.objects.count(), ResearchSessionMaterialization.objects.count())
        result = self.verify()
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['export_response_count'], 1)
        self.assertEqual(result['coverage_attempt_count'], 1)
        self.assertTrue(result['checks']['automatic_grades_match_frozen_scoring'])
        self.assertTrue(result['checks']['export_rows_match_authoritative_facts'])
        after = (list(Attempt.objects.values()), list(SubmissionEvent.objects.values()), list(GradeRecord.objects.values()),
                 RecordingCoverageRun.objects.count(), ResearchSessionMaterialization.objects.count())
        self.assertEqual(before, after)
        rendered = json.dumps(result)
        for private in (self.student.username, self.student.student_number, str(self.attempt.submission_event.submission_token), 'final_answer'):
            self.assertNotIn(private, rendered)

    def test_wrong_effective_grade_and_attempt_projection_fail_without_repair(self):
        GradeRecord.objects.filter(submission__attempt=self.attempt).update(score=0, is_correct=False)
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['automatic_grades_match_frozen_scoring'])
        self.assertFalse(result['raw_fact_integrity_failed'])
        self.assertEqual(GradeRecord.objects.get(submission__attempt=self.attempt).score, 0)

    def test_tampered_materialized_export_row_fails(self):
        row = ResearchSessionResponse.objects.get()
        row.row[12] = 'tampered-private-answer'
        row.save(update_fields=['row'])
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['export_rows_match_authoritative_facts'])
        self.assertNotIn('tampered-private-answer', json.dumps(result))

    def test_undrained_materialization_is_not_ready_and_is_not_repaired(self):
        ResearchSessionMaterialization.objects.update(is_current=False)
        before = ResearchSessionMaterialization.objects.count()
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'], 'not_ready')
        self.assertFalse(result['checks']['current_materializations_ready'])
        self.assertEqual(ResearchSessionMaterialization.objects.count(), before)

    def test_incomplete_ledger_is_not_ready_not_integrity_alarm(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['incomplete'] = True
        result = self.verify(ledger=ledger)
        self.assertEqual(result['status'], 'not_ready')
        self.assertFalse(result['passed'])

    def test_fixture_and_ledger_scope_mismatches_are_rejected(self):
        for field in ('task_id', 'source_config', 'capture_protocol'):
            ledger = copy.deepcopy(self.ledger)
            ledger[field] = 'different'
            with self.subTest(field=field), self.assertRaises(CommandError):
                self.verify(ledger=ledger)
        fixture = {**self.fixture, 'course_id': self.course.pk + 10000}
        with self.assertRaises(CommandError):
            self.verify(fixture=fixture)
        ledger = copy.deepcopy(self.ledger)
        ledger['sessions'][str(self.session.pk)]['username_sha256'] = 'wrong'
        with self.assertRaises(CommandError):
            self.verify(ledger=ledger)

    def test_answer_hash_mismatch_fails_before_export(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['sessions'][str(self.session.pk)]['answers'][str(self.attempt.pk)]['state_sha256'] = 'wrong'
        result = self.verify(ledger=ledger)
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['ledger_answers_match_authoritative_submissions'])
        self.assertTrue(result['raw_fact_integrity_failed'])
        self.assertEqual(result['status'], 'raw_fact_mismatch')

    def test_non_isolated_database_rejected_before_files_are_read(self):
        with self.assertRaisesRegex(CommandError, 'kt_e2e'):
            call_command('verify_school_architecture_results', manifest='never-read', output='never-created', stdout=StringIO())

    def test_manifest_claim_hash_must_match_and_output_is_aggregate(self):
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / 'fixture.json'
            manifest.write_text(json.dumps(self.fixture), encoding='utf-8')
            manifest.with_suffix('.ledger.json').write_text(json.dumps(self.ledger), encoding='utf-8')
            claim = manifest.parent / f'.school-task-{self.task.pk}.claim.json'
            claim.write_text(json.dumps({'task_id': self.task.pk, 'run_id': self.fixture['run_id'], 'manifest_sha256': 'wrong'}))
            output = Path(directory) / 'aggregate.json'
            with patch('apps.experiments.management.commands.verify_school_architecture_results.require_isolated_database'):
                with self.assertRaisesRegex(CommandError, 'binding'):
                    call_command('verify_school_architecture_results', manifest=str(manifest), output=str(output), stdout=StringIO())
                self.assertFalse(output.exists())
                claim.write_text(json.dumps({'task_id': self.task.pk, 'run_id': self.fixture['run_id'],
                                            'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}))
                call_command('verify_school_architecture_results', manifest=str(manifest), output=str(output), stdout=StringIO())
            self.assertTrue(json.loads(output.read_text())['passed'])

    def test_reader_failure_is_sanitized_and_no_derived_rows_survive(self):
        with patch('apps.experiments.management.commands.verify_school_architecture_results.read_research_bundle',
                   side_effect=RuntimeError('private-account-and-answer')):
            result = self.verify()
        self.assertFalse(result['passed'])
        self.assertEqual(result['status'], 'read_failed')
        self.assertNotIn('private-account-and-answer', json.dumps(result))
        self.assertEqual(RecordingCoverageRun.objects.count(), 0)

    def test_pending_manual_short_grade_has_insufficient_outcome_without_false_corruption(self):
        from apps.assessment.feedback_services import initial_grading_result
        from apps.assessment.recording_capture_services import ensure_capture_contract_v2
        # Publish a separate short-answer task; never mutate a frozen question snapshot.
        self.question.question_type = 'short_answer'
        self.question.save(update_fields=['question_type'])
        self.question.options.all().delete()
        self.experiment, self.task = self.publish_task(name='Pending manual grade', feedback_mode=self.feedback_mode)
        self.assertEqual(self.start().status_code, 201)
        self.session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.complete_session(self.session)
        self.attempt = self.session.attempts.select_related('assignment__snapshot').get()
        # Construct a deliberately pending v2 fixture; production contracts remain immutable.
        ExperimentSession.objects.filter(pk=self.session.pk).update(capture_contract=ensure_capture_contract_v2())
        self.session.refresh_from_db()
        self.attempt.session = self.session
        expected = initial_grading_result(self.attempt, True, 1)
        self.assertEqual(expected.status, 'pending')
        response = create_response_snapshot(attempt=self.attempt, content='B')
        Attempt.objects.filter(pk=self.attempt.pk).update(final_response_snapshot=response, score=None, is_correct=None)
        SubmissionEvent.objects.filter(attempt=self.attempt).update(final_response_snapshot=response,
            grading_status='pending', score=None, is_correct=None)
        GradeRecord.objects.filter(submission__attempt=self.attempt).update(status='pending', validity_status='candidate',
            score=None, is_correct=None, max_score=expected.max_score, grading_method=expected.method, graded_at=None)
        self.fixture.update(task_id=self.task.pk, experiment_id=self.experiment.pk)
        self.ledger.update(task_id=self.task.pk, sessions={str(self.session.pk): {
            'username_sha256': digest(self.student.username), 'complete': True, 'answers': {str(self.attempt.pk): {
                'question_type': 'short_answer', 'state_sha256': digest({'question_type': 'short_answer', 'value': 'B'}),
                'target_event_id': str(self.attempt.final_response_event_id), 'response_version': self.attempt.response_version,
                'submission_token': str(self.attempt.submission_event.submission_token)}}}})
        self.materialize()
        result = self.verify()
        self.assertTrue(result['checks']['pending_grades_are_not_zero_or_correctness'], result)
        self.assertTrue(result['checks']['coverage_outcome_matches_grade_availability'], result)
        self.assertTrue(result['checks']['coverage_has_no_authoritative_contradictions'], result)
        self.assertEqual(result['pending_grade_count'], 1)
        self.assertEqual(result['coverage_outcome_usable_count'], 0)

    def test_authoritative_final_snapshot_mismatch_fails_coverage_without_repair(self):
        wrong = create_response_snapshot(attempt=self.attempt, content='A')
        Attempt.objects.filter(pk=self.attempt.pk).update(final_response_snapshot=wrong)
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['coverage_has_no_authoritative_contradictions'])
        self.assertEqual(Attempt.objects.get(pk=self.attempt.pk).final_response_snapshot_id, wrong.pk)

    def test_cross_session_answers_in_ledger_do_not_satisfy_binding(self):
        ledger = copy.deepcopy(self.ledger)
        ledger['sessions'][str(self.session.pk)]['answers']['999999999'] = ledger['sessions'][str(self.session.pk)]['answers'].pop(str(self.attempt.pk))
        with self.assertRaisesRegex(CommandError, 'answer.*binding'):
            self.verify(ledger=ledger)
