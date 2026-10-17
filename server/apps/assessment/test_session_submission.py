"""A receipt freezes a saved paper; workers commit it exactly once."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.question_bank.models import Question
from apps.api.test_support import domain_payload
from apps.accounts.models import User
from . import test_capture_v2 as fixtures
from .models import AssessmentEvent, Attempt, ConfidenceAssignment, ExperimentSession, GradeRecord, SubmissionEvent
from .event_batch_services import ProcessRequestError


class SessionSubmissionTests(TestCase):
    transition = fixtures.CaptureV2Tests.transition
    ingest = fixtures.CaptureV2Tests.ingest

    def setUp(self):
        from .confidence_services import ensure_confidence_assignment
        fixtures.CaptureV2Tests.setUp(self)
        ExperimentSession.objects.filter(pk=self.session.pk).update(assessment_flow_version=1)
        self.session.refresh_from_db()
        self.stream = uuid.uuid4()
        for attempt in self.session.attempts.all():
            ensure_confidence_assignment(attempt)

    def confidence(self, attempt, **values):
        ConfidenceAssignment.objects.filter(attempt=attempt).update(**values)
        return ConfidenceAssignment.objects.get(attempt=attempt)

    def save_answer(self, kind=Question.Type.SINGLE, answer='A'):
        from .services import save_draft
        attempt = self.attempts[kind]
        empty = '' if kind in (Question.Type.SHORT, Question.Type.FILL_BLANK, Question.Type.NUMERIC) else None
        event = self.transition(attempt, empty, answer)
        self.ingest([event])
        save_draft(attempt.pk, None, target_event_id=event['event_id'], expected_version=0)
        attempt.refresh_from_db()
        return attempt

    def document(self):
        return {'submission_token': str(uuid.uuid4()), 'client_clicked_at': timezone.now().isoformat(),
                'expected_versions': [{'attempt_id': row.pk, 'response_version': row.response_version,
                                       'target_event_id': str(row.active_response_event_id) if row.active_response_event_id else None}
                                      for row in self.session.attempts.order_by('pk')]}

    def accept(self, document=None, **kwargs):
        from .session_submission_services import accept_session_submission
        return accept_session_submission(student=self.student, session_id=self.session.pk,
                                         document=document or self.document(), **kwargs)

    def work(self):
        from .session_submission_services import claim_session_submission, run_session_submission
        job = claim_session_submission(worker_id='test-worker')
        self.assertIsNotNone(job)
        return run_session_submission(job)

    @override_settings(ASSESSMENT_REQUEST_TIMING_ENABLED=True)
    def test_submission_phase_timing_covers_receipt_and_worker_without_material(self):
        from config.capture_timing import pending_timing
        from .session_submission_services import claim_session_submission, run_session_submission
        state = {}
        token = pending_timing.set(state)
        with self.assertLogs('kt.session_submission_timing', level='WARNING') as logs:
            try:
                job = self.accept()
            finally:
                pending_timing.reset(token)
            claimed = claim_session_submission(worker_id='test-worker')
            result = run_session_submission(claimed)
        rows = [json.loads(row.message) for row in logs.records]
        self.assertEqual([row['phase'] for row in rows], [
            'submission_material', 'submission_accept', 'submission_claim', 'submission_process'])
        self.assertEqual(result.status, 'completed')
        for row in rows:
            self.assertGreaterEqual(row['seconds'], 0)
            self.assertNotIn('manifest', row)
            self.assertNotIn('document', row)
            self.assertNotIn('sql', row)
        self.assertEqual(rows[-1]['submission_id'], str(job.pk))
        self.assertEqual(rows[-1]['status'], 'completed')
        self.assertEqual(set(state['capture_metrics']), {'submission_material_seconds', 'submission_accept_seconds'})

    def test_status_query_records_its_authorized_lookup_phase(self):
        from config.capture_timing import TIMING_ENV_KEY
        from .api.session_submission import StudentSessionSubmissionView
        from rest_framework.test import APIRequestFactory
        self.accept()
        state = {}
        request = APIRequestFactory().get('/')
        request.user = self.student
        request.META[TIMING_ENV_KEY] = state
        response = StudentSessionSubmissionView().get(request, self.session.pk)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(state.get('capture_metrics', {}).get('submission_status_seconds', -1), 0)

    def test_acceptance_freezes_without_grading_or_per_attempt_writes(self):
        self.save_answer()
        with CaptureQueriesContext(connection) as queries:
            job = self.accept()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'submitting')
        self.assertEqual(self.session.terminal_at, job.accepted_at)
        self.assertIsNone(self.session.completed_at)
        self.assertFalse(SubmissionEvent.objects.exists())
        self.assertFalse(GradeRecord.objects.exists())
        self.assertFalse(any(sql['sql'].startswith('UPDATE "assessment_attempt"') for sql in queries))
        self.assertFalse(any('FROM "assessment_assessmentevent"' in sql['sql'] for sql in queries))
        if connection.vendor == 'postgresql':
            self.assertTrue(any('FOR NO KEY UPDATE' in sql['sql'] for sql in queries))

    def test_task_receipt_targets_existing_session_complete_page(self):
        from .session_submission_services import submission_receipt
        job = self.accept()
        job.session.class_task_id = 123
        self.assertEqual(submission_receipt(job)['next_url'],
                         reverse('assessment:complete', args=[self.session.pk]))

    def test_required_check_blocks_freeze_until_real_completion(self):
        from .research_tool_services import record_tool_action
        self.version.configuration = {'research_tools': {'check_enabled': True, 'rules': [{
            'id': 'check1', 'tool': 'check', 'trigger': 'item_enter', 'question_code': '',
            'after_seconds': 0, 'max_exposures': 1, 'required': True, 'hint_id': ''}]}}
        self.version.save(update_fields=['configuration'])
        attempt = self.save_answer()
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]),
            data=json.dumps(self.document()), content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'in_progress')
        self.assertFalse(hasattr(self.session, 'submission_job'))
        self.assertFalse(AssessmentEvent.objects.filter(event_type='research_tool').exists())
        self.assertFalse(SubmissionEvent.objects.exists())
        record_tool_action(student=self.student, attempt_id=attempt.pk, tool='check', stage='finished',
            operation_id=str(uuid.uuid4()), data={'rule_id': 'check1'}, initiation_type='task_required',
            answer_event_id=attempt.active_response_event_id)
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]),
            data=json.dumps(self.document()), content_type='application/json')
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(AssessmentEvent.objects.filter(event_type='research_tool').count(), 1)

    def test_required_tools_do_not_force_answers_or_fabricate_usage(self):
        self.version.configuration = {'research_tools': {'check_enabled': True, 'rules': [{
            'id': 'check1', 'tool': 'check', 'trigger': 'item_enter', 'question_code': '',
            'after_seconds': 0, 'max_exposures': 1, 'required': True, 'hint_id': ''}]}}
        self.version.save(update_fields=['configuration'])
        self.accept()
        self.work()
        self.assertEqual(self.session.attempts.filter(status='unanswered').count(), 6)
        self.assertFalse(AssessmentEvent.objects.filter(event_type='research_tool').exists())

    def test_required_sampled_confidence_real_tool_bundle_allows_whole_submission(self):
        from .confidence_services import ensure_confidence_assignment
        from .research_tool_services import content_version
        self.version.configuration = {'confidence_mode': 'sampled', 'confidence_probability': '1',
            'confidence_sampling_unit': 'attempt', 'research_tools': {'rules': [{
                'id': 'confidence1', 'tool': 'confidence', 'trigger': 'item_enter', 'question_code': '',
                'after_seconds': 0, 'max_exposures': 1, 'required': True, 'hint_id': ''}]}}
        self.version.save(update_fields=['configuration'])
        ConfidenceAssignment.objects.filter(attempt__session=self.session).delete()
        for candidate in self.session.attempts.all():
            ensure_confidence_assignment(candidate)
        attempt = self.save_answer()
        assignment = ConfidenceAssignment.objects.get(attempt=attempt)
        self.assertEqual(assignment.condition, 'prompt')
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]),
            data=json.dumps(self.document()), content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        attempt.session.version = self.version
        for sequence, stage in enumerate(('requested', 'presented', 'responded'), start=2):
            operation_id = str(uuid.uuid4())
            data = {'rule_id': 'confidence1', **({'probability': 0} if stage == 'responded' else {})}
            observed = self.transition(attempt, 'A', 'A', sequence=sequence)
            observed.update(event_type='research_tool_requested', event_version=1)
            observed['payload'] = {'capture_version': 'capture-v2', 'operation_id': operation_id,
                'tool': 'confidence', 'stage': stage, 'data': data,
                'answer_event_id': str(attempt.active_response_event_id),
                'content_version': content_version(attempt), 'initiation_type': 'task_required'}
            response = self.client.post(reverse('assessment_process:research_tools', args=[attempt.pk]),
                data=json.dumps({'tool': 'confidence', 'stage': stage, 'operation_id': operation_id,
                    'data': data, 'answer_event_id': str(attempt.active_response_event_id),
                    'initiation_type': 'task_required', 'capture_bundle': {'version': 1,
                        'client_instance_id': str(self.stream), 'event_boundary': sequence,
                        'events': [observed], 'checkpoints': []}}), content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
        assignment.refresh_from_db()
        self.assertEqual(assignment.draft_probability, 0)
        self.assertEqual(assignment.draft_answer_event_id, attempt.active_response_event_id)
        self.assertIsNone(assignment.probability)
        self.assertIsNone(assignment.committed_at)
        self.assertIsNone(assignment.prompt_shown_at)
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt,
            event_type__in=['confidence_committed', 'confidence_prompt_shown']).exists())
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]),
            data=json.dumps(self.document()), content_type='application/json')
        self.assertEqual(response.status_code, 202, response.content)
        self.work()
        assignment.refresh_from_db()
        self.assertEqual(assignment.probability, 0)
        self.assertEqual(assignment.response_status, 'committed')
        self.assertIsNotNone(assignment.committed_at)
        self.assertIsNone(assignment.prompt_shown_at)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt,
            event_type='confidence_committed').count(), 1)

    def test_same_material_from_another_tab_recovers_effective_receipt(self):
        document = self.document()
        first = self.accept(document)
        document['submission_token'] = str(uuid.uuid4())
        document['client_clicked_at'] = timezone.now().isoformat()
        self.assertEqual(self.accept(document).pk, first.pk)
        document['expected_versions'][0]['response_version'] += 1
        with self.assertRaises(ProcessRequestError):
            self.accept(document)

    def test_stale_or_incomplete_manifest_is_rejected_without_freezing(self):
        self.save_answer()
        document = self.document()
        document['expected_versions'][0]['response_version'] += 1
        with self.assertRaises(ProcessRequestError):
            self.accept(document)
        document = self.document()
        document['expected_versions'].pop()
        with self.assertRaises(ProcessRequestError):
            self.accept(document)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'in_progress')

    def test_worker_commits_answer_once_and_keeps_blanks_unanswered(self):
        from .session_submission_services import run_session_submission
        attempt = self.save_answer()
        job = self.accept()
        self.work()
        self.assertEqual(SubmissionEvent.objects.count(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_answer, 'A')
        self.assertEqual(attempt.final_response_event_id, attempt.active_response_event_id)
        self.assertEqual(self.session.attempts.exclude(pk=attempt.pk).filter(status='unanswered').count(), 5)
        self.session.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.session.status, 'completed')
        self.assertEqual(job.status, 'completed')
        self.assertGreaterEqual(job.completed_at, job.accepted_at)
        self.assertEqual(SubmissionEvent.objects.get().server_received_at, job.accepted_at)
        run_session_submission(job)
        self.assertEqual(GradeRecord.objects.count(), 1)

    def test_paper_commit_batches_shared_reads_and_session_projection_writes(self):
        from .session_submission_services import claim_session_submission, run_session_submission
        for kind, answer in ((Question.Type.SINGLE, 'A'), (Question.Type.SHORT, 'explanation'),
                             (Question.Type.NUMERIC, '7')):
            self.stream = uuid.uuid4()
            attempt = self.save_answer(kind, answer)
            self.confidence(attempt, condition='prompt', response_status='assigned',
                draft_probability=0, draft_answer_event_id=attempt.active_response_event_id)
        job = self.accept()
        claimed = claim_session_submission(worker_id='query-budget-worker')
        self.session.refresh_from_db()
        first_sequence = self.session.next_ingest_sequence_no
        with CaptureQueriesContext(connection) as queries:
            result = run_session_submission(claimed)
        self.assertEqual(result.status, 'completed')
        sql = [row['sql'] for row in queries]
        confidence_reads = [query for query in sql if query.startswith('SELECT')
                            and 'FROM "assessment_confidenceassignment"' in query]
        self.assertEqual(len(confidence_reads), 1, confidence_reads)
        if connection.vendor == 'postgresql':
            self.assertIn('FOR UPDATE', confidence_reads[0])
        projection_updates = [query for query in sql
                              if query.startswith('UPDATE "assessment_experimentsession"')
                              and '"research_source_revision"' not in query]
        self.assertEqual(len(projection_updates), 1, projection_updates)
        self.assertEqual(sum('MAX(' in query and 'assessment_assessmentevent' in query for query in sql), 1)
        self.assertFalse(any(query.startswith('UPDATE "assessment_submissionevent"') for query in sql))
        self.session.refresh_from_db()
        self.assertEqual(self.session.submission_count, 3)
        self.assertEqual(self.session.result_revision, 1)
        events = list(self.session.assessment_events.filter(
            server_ingest_sequence_no__gte=first_sequence).order_by('server_ingest_sequence_no'))
        self.assertEqual([event.server_ingest_sequence_no for event in events],
                         list(range(first_sequence, self.session.next_ingest_sequence_no)))
        self.assertEqual(events[-1].event_type, 'session_completed')
        boundaries = []
        for submission in SubmissionEvent.objects.order_by('attempt_id'):
            attempt = submission.attempt
            committed = next(event for event in events
                             if event.attempt_id == attempt.pk and event.event_type == 'response_committed')
            boundary = attempt.formal_capture_boundary['server_ingest_sequence_no']
            self.assertEqual(boundary, committed.server_ingest_sequence_no - 1)
            confidence = next(event for event in events
                              if event.attempt_id == attempt.pk and event.event_type == 'confidence_committed')
            self.assertEqual(boundary, confidence.server_ingest_sequence_no)
            self.assertEqual(submission.server_received_at, job.accepted_at)
            self.assertGreaterEqual(submission.committed_at, job.accepted_at)
            boundaries.append(boundary)
        self.assertEqual(len(set(boundaries)), 3)

    def test_first_formal_boundary_tracks_evidence_when_ingest_counter_has_a_gap(self):
        attempt = self.save_answer(Question.Type.SHORT, 'explanation')
        self.accept()
        last_event = self.session.assessment_events.order_by('-server_ingest_sequence_no').first()
        self.session.refresh_from_db()
        ExperimentSession.objects.filter(pk=self.session.pk).update(
            next_ingest_sequence_no=self.session.next_ingest_sequence_no + 10)
        self.work()
        attempt.refresh_from_db()
        self.assertEqual(attempt.formal_capture_boundary['server_ingest_sequence_no'],
                         last_event.server_ingest_sequence_no)

    def test_worker_does_not_revalidate_later_deadlines(self):
        self.save_answer()
        job = self.accept()
        ExperimentSession.objects.filter(pk=self.session.pk).update(deadline_at=timezone.now() - timedelta(days=1))
        self.work()
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')

    def test_worker_rollback_and_retry_do_not_double_grade(self):
        from .session_submission_services import claim_session_submission, run_session_submission
        attempt = self.save_answer()
        confidence = self.confidence(attempt, condition='prompt', response_status='assigned',
            draft_probability=0, draft_answer_event_id=attempt.active_response_event_id)
        self.accept()
        job = claim_session_submission(worker_id='first')
        self.session.refresh_from_db()
        original_sequence = self.session.next_ingest_sequence_no
        original_event_count = self.session.assessment_events.count()
        with self.assertLogs('apps.assessment.session_submission_services', level='ERROR'):
            with patch('apps.assessment.session_submission_services._finalize_session', side_effect=RuntimeError('transient')):
                run_session_submission(job)
        self.assertFalse(SubmissionEvent.objects.exists())
        self.session.refresh_from_db()
        confidence.refresh_from_db()
        self.assertEqual(self.session.next_ingest_sequence_no, original_sequence)
        self.assertEqual(self.session.submission_count, 0)
        self.assertEqual(self.session.assessment_events.count(), original_event_count)
        self.assertEqual(confidence.response_status, 'assigned')
        self.assertIsNone(confidence.probability)
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        type(job).objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
        self.work()
        self.assertEqual(GradeRecord.objects.count(), 1)

    def test_expired_lease_recovers_and_stale_worker_cannot_commit(self):
        from .session_submission_services import claim_session_submission, run_session_submission
        self.save_answer()
        self.accept()
        stale = claim_session_submission(worker_id='old')
        type(stale).objects.filter(pk=stale.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        current = claim_session_submission(worker_id='new')
        self.assertNotEqual(current.lease_token, stale.lease_token)
        run_session_submission(stale)
        self.assertFalse(SubmissionEvent.objects.exists())
        run_session_submission(current)
        self.assertEqual(SubmissionEvent.objects.count(), 1)

    def test_manual_short_answer_retains_pending_grade(self):
        attempt = self.save_answer(Question.Type.SHORT, 'explanation')
        self.accept()
        self.work()
        grade = GradeRecord.objects.get(submission__attempt=attempt)
        self.assertEqual(grade.status, 'pending')
        self.assertIsNone(grade.is_correct)
        self.assertIsNone(grade.score)


    def test_received_at_cannot_be_replaced_by_client_clock(self):
        self.session.deadline_at = timezone.now() - timedelta(seconds=1)
        self.session.save(update_fields=['deadline_at'])
        document = self.document()
        document['client_clicked_at'] = (timezone.now() - timedelta(days=1)).isoformat()
        with self.assertRaises(ProcessRequestError):
            self.accept(document)

    def test_frozen_material_remains_authoritative_even_if_projection_changes(self):
        attempt = self.save_answer()
        self.accept()
        Attempt.objects.filter(pk=attempt.pk).update(draft_answer='B', active_response_event=None,
                                                    current_response_snapshot=None, response_version=22)
        self.work()
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_answer, 'A')
        self.assertEqual(attempt.response_version, 1)
        self.assertIsNotNone(attempt.final_response_event_id)

    def test_zero_confidence_is_committed_and_missing_confidence_is_skipped(self):
        attempt = self.save_answer()
        confidence = self.confidence(attempt, condition='prompt',
            assignment_unit='attempt', assignment_version='v1', response_status='prompt_shown',
            prompt_shown_at=timezone.now(), draft_probability=0, draft_answer_event_id=attempt.active_response_event_id)
        self.stream = uuid.uuid4()
        answered = self.save_answer(Question.Type.SHORT, 'explanation')
        missing = self.confidence(answered, condition='prompt',
            assignment_unit='attempt', assignment_version='v1', response_status='assigned')
        self.accept()
        self.work()
        confidence.refresh_from_db()
        missing.refresh_from_db()
        self.assertEqual(confidence.response_status, 'committed')
        self.assertEqual(confidence.probability, 0)
        self.assertEqual(missing.response_status, 'skipped')
        self.assertIsNone(missing.probability)
        event = AssessmentEvent.objects.get(attempt=answered, event_type='confidence_skipped')
        self.assertEqual(event.event_version, 3)
        self.assertEqual(event.payload['reason_code'], 'optional_unfilled')
        self.assertFalse(AssessmentEvent.objects.filter(attempt=answered, event_type='confidence_prompt_shown').exists())

    def test_unanswered_confidence_retains_raw_zero_and_missing_without_formalizing(self):
        blank = self.attempts[Question.Type.SINGLE]
        selected = self.confidence(blank, condition='prompt', response_status='assigned',
            draft_probability=0, draft_answer_event_id=None)
        missing_attempt = self.attempts[Question.Type.SHORT]
        missing = self.confidence(missing_attempt, condition='prompt', response_status='assigned')
        observed = self.transition(blank, None, None)
        observed.update(event_type='confidence_observed', event_version=1)
        observed['payload'] = {'capture_version': 'capture-v2', 'operation_id': str(uuid.uuid4()),
            'confidence_assignment_id': str(selected.pk), 'stage': 'responded',
            'answer_event_id': None, 'probability': 0, 'feedback_phase': 'before_feedback'}
        self.ingest([observed])
        raw = AssessmentEvent.objects.get(event_id=observed['event_id'])
        original_payload = raw.payload.copy()
        job = self.accept()
        frozen = next(row for row in job.manifest['attempts'] if row['attempt_id'] == blank.pk)
        self.assertEqual(frozen['confidence_probability'], 0)
        self.work()
        for attempt, assignment in ((blank, selected), (missing_attempt, missing)):
            attempt.refresh_from_db()
            assignment.refresh_from_db()
            self.assertEqual(attempt.status, Attempt.Status.UNANSWERED)
            self.assertEqual(assignment.response_status, 'assigned')
            self.assertIsNone(assignment.probability)
            self.assertIsNone(assignment.committed_at)
            self.assertIsNone(assignment.prompt_shown_at)
            self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt,
                event_type__in=['confidence_committed', 'confidence_skipped', 'confidence_prompt_shown']).exists())
            self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertEqual(selected.draft_probability, 0)
        self.assertIsNone(selected.draft_answer_event_id)
        self.assertIsNone(missing.draft_probability)
        raw.refresh_from_db()
        self.assertEqual(raw.payload, original_payload)

    def test_changed_answer_invalidates_old_confidence_without_claiming_student_skip(self):
        from .services import save_draft
        attempt = self.save_answer()
        old_event_id = attempt.active_response_event_id
        self.confidence(attempt, condition='prompt',
            assignment_unit='attempt', assignment_version='v1', response_status='prompt_shown',
            prompt_shown_at=timezone.now(), draft_probability=60, draft_answer_event_id=old_event_id)
        changed = self.transition(attempt, 'A', 'B', parent=str(old_event_id), sequence=2)
        self.ingest([changed])
        save_draft(attempt.pk, None, target_event_id=changed['event_id'], expected_version=1)
        confidence = ConfidenceAssignment.objects.get(attempt=attempt)
        self.assertIsNone(confidence.draft_probability)
        self.assertEqual(confidence.draft_missing_reason, 'answer_changed')
        job = self.accept()
        row = next(item for item in job.manifest['attempts'] if item['attempt_id'] == attempt.pk)
        self.assertEqual(row['confidence_missing_reason'], 'answer_changed')
        self.work()
        event = AssessmentEvent.objects.get(attempt=attempt, event_type='confidence_skipped')
        self.assertEqual(event.event_version, 3)
        self.assertEqual(event.payload['reason_code'], 'answer_changed')
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt, event_type='confidence_prompt_shown').exists())

    def test_expired_final_lease_exposes_failed_receipt_with_frozen_session(self):
        from .session_submission_services import claim_session_submission
        job = self.accept()
        type(job).objects.filter(pk=job.pk).update(status='running', attempts=3, lease_token=uuid.uuid4(),
                                                  lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(claim_session_submission(worker_id='recovery'))
        job.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(self.session.status, 'submitting')

    def test_unvalidated_numeric_saved_answer_is_retained_pending(self):
        attempt = self.save_answer(Question.Type.NUMERIC, 'not a number')
        self.accept()
        self.work()
        self.assertEqual(SubmissionEvent.objects.get(attempt=attempt).final_answer, 'not a number')
        grade = GradeRecord.objects.get(submission__attempt=attempt)
        self.assertEqual(grade.status, 'pending')
        self.assertIsNone(grade.is_correct)
        self.assertIsNone(grade.score)

    def test_existing_finish_url_returns_202_and_poll_recovers_receipt(self):
        self.save_answer()
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]),
            data=json.dumps(self.document()), content_type='application/json')
        self.assertEqual(response.status_code, 202, response.content)
        receipt = domain_payload(response)
        self.assertEqual(receipt['status'], 'queued')
        self.assertEqual(receipt['next_url'], reverse('assessment:complete', args=[self.session.pk]))
        self.assertFalse(SubmissionEvent.objects.exists())
        poll = self.client.get(receipt['status_url'])
        self.assertEqual(poll.status_code, 200, poll.content)
        self.assertEqual(domain_payload(poll)['submission_id'], receipt['submission_id'])
        self.work()
        completed = domain_payload(self.client.get(receipt['status_url']))
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['accepted_at'], receipt['accepted_at'])

    def test_receipt_projects_completion_time_and_safe_failure_without_internal_details(self):
        from .session_submission_services import submission_receipt
        job = self.accept()
        receipt = submission_receipt(job)
        self.assertIsNone(receipt['completed_at'])
        self.assertIsNone(receipt['error'])
        type(job).objects.filter(pk=job.pk).update(status='failed', error_code='secret_database_host:password')
        job.refresh_from_db()
        receipt = submission_receipt(job)
        self.assertEqual(receipt['error']['code'], 'submission_needs_verification')
        self.assertIn('核验', receipt['error']['message'])
        self.assertNotIn('secret_database_host', json.dumps(receipt))
        type(job).objects.filter(pk=job.pk).update(status='queued', error_code='')
        self.work()
        job.refresh_from_db()
        receipt = submission_receipt(job)
        self.assertEqual(receipt['completed_at'], job.completed_at.isoformat())
        self.assertIsNone(receipt['error'])

    def test_status_api_exposes_safe_failure_and_completion_fields(self):
        job = self.accept()
        type(job).objects.filter(pk=job.pk).update(status='failed', error_code='worker_lease_exhausted')
        response = self.client.get(reverse('assessment_api:student-session-submission', args=[self.session.pk]))
        payload = domain_payload(response)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payload['completed_at'])
        self.assertEqual(payload['error']['code'], 'submission_needs_verification')
        self.assertNotIn('worker_lease_exhausted', json.dumps(payload))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_status_receipt_cannot_be_read_by_another_student(self):
        from .test_support import accept_research_consent
        self.accept()
        other = User.objects.create_user(username='other-submission-student', role='student')
        accept_research_consent(other)
        self.client.force_login(other)
        response = self.client.get(reverse('assessment_api:student-session-submission', args=[self.session.pk]))
        self.assertEqual(response.status_code, 404)

    def test_deadline_auto_submit_preserves_bound_zero_confidence(self):
        from .services import _terminate_for_deadline
        attempt = self.save_answer()
        # A single existing item keeps this focused on deadline confidence semantics.
        ConfidenceAssignment.objects.filter(attempt__session=self.session).exclude(attempt=attempt).delete()
        self.session.attempts.exclude(pk=attempt.pk).delete()
        confidence = self.confidence(attempt, condition='prompt',
            assignment_unit='attempt', assignment_version='v1', response_status='assigned',
            draft_probability=0, draft_answer_event_id=attempt.active_response_event_id)
        _terminate_for_deadline(self.session, now=timezone.now(), reason='deadline_expired')
        confidence.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'expired')
        self.assertEqual(SubmissionEvent.objects.get(attempt=attempt).final_answer, 'A')
        self.assertEqual(confidence.response_status, 'committed')
        self.assertEqual(confidence.probability, 0)
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt, event_type='confidence_prompt_shown').exists())

    def test_deadline_auto_submit_marks_optional_unfilled_with_versioned_reason(self):
        from .services import _terminate_for_deadline
        attempt = self.save_answer()
        ConfidenceAssignment.objects.filter(attempt__session=self.session).exclude(attempt=attempt).delete()
        self.session.attempts.exclude(pk=attempt.pk).delete()
        self.confidence(attempt, condition='prompt',
            assignment_unit='attempt', assignment_version='v1', response_status='assigned')
        _terminate_for_deadline(self.session, now=timezone.now(), reason='deadline_expired')
        event = AssessmentEvent.objects.get(attempt=attempt, event_type='confidence_skipped')
        self.assertEqual(event.event_version, 3)
        self.assertEqual(event.payload['reason_code'], 'optional_unfilled')

    def test_deadline_keeps_saved_answers_when_earlier_items_are_blank(self):
        from .services import _terminate_for_deadline
        attempt = self.save_answer()
        _terminate_for_deadline(self.session, now=timezone.now(), reason='deadline_expired')
        self.assertEqual(SubmissionEvent.objects.get(attempt=attempt).final_answer, 'A')
        self.assertEqual(self.session.attempts.exclude(pk=attempt.pk).filter(status='unanswered').count(), 5)


class ConcurrentSessionSubmissionTests(TransactionTestCase):
    setUp = SessionSubmissionTests.setUp
    document = SessionSubmissionTests.document

    def test_simultaneous_tabs_create_one_effective_receipt(self):
        from .session_submission_services import accept_session_submission
        barrier = Barrier(2)
        documents = [self.document(), self.document()]
        def accept(document):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return accept_session_submission(student=self.student, session_id=self.session.pk,
                                                 document=document).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(accept, documents))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.session.submission_job.pk, ids[0])

    def test_parallel_workers_claim_one_receipt_once(self):
        from .session_submission_services import accept_session_submission, claim_session_submission
        accept_session_submission(student=self.student, session_id=self.session.pk, document=self.document())
        barrier = Barrier(2)
        def claim(worker):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                job = claim_session_submission(worker_id=worker)
                return job.pk if job else None
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(claim, ['one', 'two']))
        self.assertEqual(sum(value is not None for value in claimed), 1)
