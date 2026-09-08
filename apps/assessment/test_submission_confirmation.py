"""Submission and confidence are one final, recoverable decision."""
import json
import uuid
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

from django.test import TestCase, TransactionTestCase
from django.db import connections, transaction
from django.urls import reverse

from . import test_confidence_flow as fixtures
from . import test_capture_v2 as capture_fixtures
from .confidence_services import materialize_confidence_assignments
from .models import AssessmentEvent, Attempt, ConfidenceAssignment, SubmissionEvent


class SubmissionConfirmationTests(TestCase):
    setUp = fixtures.ConfidenceFlowTests.setUp
    post_process = fixtures.ConfidenceFlowTests.post_process

    def prepare_answer(self):
        assignment = self.session.assignments.order_by('assigned_sequence_index').first()
        response = self.client.post(
            reverse('assessment:save_draft', args=[assignment.attempt.pk]),
            json.dumps({'answer': 'A'}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        return assignment.attempt

    def submit(self, attempt, confidence=None, token=None):
        payload = {'submission_token': token or str(uuid.uuid4())}
        if confidence is not None:
            payload['confidence'] = confidence
        return self.client.post(
            reverse('assessment:submit', args=[attempt.pk]),
            json.dumps(payload), content_type='application/json',
        )

    def test_materialization_preserves_mutable_confidence_status(self):
        attempt = self.prepare_answer()
        self.assertEqual(self.post_process('confidence_prompt_shown', attempt).status_code, 200)
        before = ConfidenceAssignment.objects.get(attempt=attempt)
        materialize_confidence_assignments(self.session)
        after = ConfidenceAssignment.objects.get(attempt=attempt)
        self.assertEqual(after.pk, before.pk)
        self.assertEqual(after.response_status, 'prompt_shown')
        self.assertEqual(after.prompt_shown_at, before.prompt_shown_at)

    def test_question_entry_exposes_confidence_before_submission(self):
        attempt = self.prepare_answer()
        response = self.client.get(f'/api/v1/student/sessions/{self.session.pk}/questions/{attempt.assignment_id}/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNotNone(response.json()['pending_confidence'])
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_confidence_write_failure_rolls_back_answer_and_grade(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        with patch('apps.assessment.confidence_services.commit_confidence', side_effect=RuntimeError('storage failure')):
            with self.assertRaises(RuntimeError):
                self.submit(attempt, {'status': 'committed', 'probability': 80})
        attempt.refresh_from_db()
        self.assertNotEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt, event_type='response_committed').exists())

    def test_missing_confidence_cannot_lock_answer(self):
        attempt = self.prepare_answer()
        response = self.submit(attempt)
        self.assertEqual(response.status_code, 400)
        attempt.refresh_from_db()
        self.assertNotEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_read_only_recovery_confirms_response_loss_without_new_events(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        token = str(uuid.uuid4())
        self.submit(attempt, {'status': 'skipped'}, token)
        before = AssessmentEvent.objects.count()
        response = self.client.get(f'/api/v1/student/attempts/{attempt.pk}/recovery/?submission_token={token}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['submission_matches'])
        self.assertEqual(response.json()['result']['status'], 'submitted')
        self.assertEqual(AssessmentEvent.objects.count(), before)

    def test_replay_token_cannot_replace_confidence_payload(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        token = str(uuid.uuid4())
        self.submit(attempt, {'status': 'skipped'}, token)
        response = self.submit(attempt, {'status': 'committed', 'probability': 80}, token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ConfidenceAssignment.objects.get(attempt=attempt).response_status, 'skipped')

    def test_question_recovery_alias_retains_formal_result_after_session_closed(self):
        from django.utils import timezone
        from .models import ExperimentSession
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        self.submit(attempt, {'status': 'skipped'})
        ExperimentSession.objects.filter(pk=self.session.pk).update(status='completed', terminal_at=timezone.now())
        before = AssessmentEvent.objects.count()
        response = self.client.get(f'/api/v1/student/sessions/{self.session.pk}/questions/{attempt.assignment_id}/recovery/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_continue'])
        self.assertEqual(response.json()['result']['status'], 'submitted')
        self.assertEqual(AssessmentEvent.objects.count(), before)

    def test_recovery_rejects_another_student_and_does_not_extend_deadline(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.accounts.models import User
        from .models import ExperimentSession
        attempt = self.prepare_answer()
        deadline = timezone.now() - timedelta(seconds=1)
        ExperimentSession.objects.filter(pk=self.session.pk).update(deadline_at=deadline)
        Attempt.objects.filter(pk=attempt.pk).update(initial_effective_deadline_at=deadline)
        before = AssessmentEvent.objects.count()
        url = f'/api/v1/student/attempts/{attempt.pk}/recovery/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_continue'])
        self.assertEqual(response.json()['code'], 'DEADLINE_EXPIRED')
        self.session.refresh_from_db()
        self.assertEqual(self.session.deadline_at, deadline)
        self.assertEqual(AssessmentEvent.objects.count(), before)
        other = User.objects.create_user(username='recovery-other', role=User.Role.STUDENT)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_final_confirmation_commits_answer_and_confidence_together(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        response = self.submit(attempt, {'status': 'committed', 'probability': 80})
        self.assertEqual(response.status_code, 200)
        attempt.refresh_from_db()
        confidence = ConfidenceAssignment.objects.get(attempt=attempt)
        self.assertEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertEqual(confidence.response_status, 'committed')
        self.assertEqual(confidence.probability, 80)
        self.assertFalse(response.json()['confidence_required'])
        self.assertEqual(response.json()['correct_answer'], 'A')
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), 1)

    def test_explicit_skip_is_saved_with_submission_and_replay_is_idempotent(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        token = str(uuid.uuid4())
        for _ in range(2):
            response = self.submit(attempt, {'status': 'skipped'}, token)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(ConfidenceAssignment.objects.get(attempt=attempt).response_status, 'skipped')
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type='confidence_skipped').count(), 1)

    def test_invalid_probability_does_not_partially_submit(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        response = self.submit(attempt, {'status': 'committed', 'probability': 75})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertEqual(ConfidenceAssignment.objects.get(attempt=attempt).response_status, 'prompt_shown')

    def test_separate_confidence_commit_cannot_bypass_final_confirmation(self):
        attempt = self.prepare_answer()
        self.post_process('confidence_prompt_shown', attempt)
        response = self.post_process('confidence_commit', attempt, {'probability': 80})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertEqual(ConfidenceAssignment.objects.get(attempt=attempt).response_status, 'prompt_shown')


class CaptureSubmissionConfirmationTests(TestCase):
    version_configuration = {'confidence_mode': 'all'}
    setUp = capture_fixtures.CaptureV2Tests.setUp
    transition = capture_fixtures.CaptureV2Tests.transition
    ingest = capture_fixtures.CaptureV2Tests.ingest

    def test_confidence_from_prior_answer_cannot_be_committed_to_new_head(self):
        from apps.question_bank.models import Question
        from .confidence_services import mark_confidence_prompt_shown
        from .services import save_draft, submit_attempt, SubmissionValidationError
        self.stream = uuid.uuid4()
        materialize_confidence_assignments(self.session)
        attempt = self.attempts[Question.Type.SINGLE]
        first = self.transition(attempt, None, 'A')
        second = self.transition(attempt, 'A', 'B', parent=first['event_id'], sequence=2)
        self.ingest([first, second])
        save_draft(attempt.pk, None, target_event_id=first['event_id'], expected_version=0)
        mark_confidence_prompt_shown(attempt_id=attempt.pk, student=self.student)
        save_draft(attempt.pk, None, target_event_id=second['event_id'], expected_version=1)
        with self.assertRaises(SubmissionValidationError):
            submit_attempt(attempt.pk, uuid.uuid4(), target_event_id=second['event_id'], confidence_response={
                'status': 'committed', 'probability': 80, 'answer_event_id': first['event_id'],
            })
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        result = submit_attempt(attempt.pk, uuid.uuid4(), target_event_id=second['event_id'], confidence_response={
            'status': 'skipped', 'answer_event_id': second['event_id'],
        })
        self.assertEqual(result.final_answer, 'B')
        self.assertEqual(str(result.final_response_event_id), second['event_id'])


class ConfidenceLockOrderTests(TransactionTestCase):
    setUp = fixtures.ConfidenceFlowTests.setUp

    def test_showing_prompt_waits_for_session_before_locking_answer(self):
        from .confidence_services import mark_confidence_prompt_shown
        from .models import ExperimentSession
        attempt = self.session.attempts.first()
        started = Event()

        def show_prompt():
            connections.close_all()
            started.set()
            try:
                return mark_confidence_prompt_shown(attempt_id=attempt.pk, student=self.student).response_status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                ExperimentSession.objects.select_for_update().get(pk=self.session.pk)
                pending = pool.submit(show_prompt)
                self.assertTrue(started.wait(3))
                time.sleep(0.25)
                Attempt.objects.select_for_update(nowait=True).get(pk=attempt.pk)
                ConfidenceAssignment.objects.select_for_update(nowait=True).get(attempt=attempt)
            self.assertEqual(pending.result(timeout=5), 'prompt_shown')
