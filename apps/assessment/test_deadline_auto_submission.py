from datetime import timedelta
from io import StringIO
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (
    AssessmentEvent,
    Attempt,
    ConfidenceAssignment,
    GradeRecord,
    SubmissionEvent,
)
from .services import (
    _terminate_for_deadline,
    record_visit,
    save_draft,
    submit_attempt,
)
from .response_snapshot_services import ResponseState, create_response_snapshot
from .test_student_api import StudentApiFixtureMixin


class DeadlineAutoSubmissionTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_concurrent_expiry_is_idempotent(self):
        session = self._session_from(self.start())
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "B")
        ready = Barrier(2)
        terminal_at = timezone.now()

        def expire():
            try:
                ready.wait(timeout=10)
                _terminate_for_deadline(session, now=terminal_at, reason="deadline_expired")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(expire) for _ in range(2)]
            for future in futures:
                future.result(timeout=30)
        session.refresh_from_db()
        self.assertEqual(session.submission_count, 1)
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), 1)
        self.assertEqual(AssessmentEvent.objects.filter(attempt=attempt, event_type="response_committed").count(), 1)

    def test_existing_formal_submission_is_preserved(self):
        session = self._session_from(self.start())
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "B")
        token = uuid.uuid4()
        submit_attempt(attempt.pk, token)
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        submission = SubmissionEvent.objects.get(attempt=attempt)
        self.assertEqual(submission.submission_token, token)
        self.assertEqual(submission.final_answer, "B")
        session.refresh_from_db()
        self.assertEqual(session.submission_count, 1)

    def test_invalid_numeric_draft_is_preserved_without_a_fake_grade(self):
        self.question.question_type = "numeric"
        self.question.correct_answer = "4"
        self.question.save(update_fields=["question_type", "correct_answer"])
        self.question.options.all().delete()
        _, task = self.publish_task(name="数值截止", feedback_mode=self.feedback_mode)
        session = self._session_from(self.start(task=task))
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "not-a-number")
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        submission = SubmissionEvent.objects.get(attempt=attempt)
        self.assertEqual(submission.final_answer, "not-a-number")
        self.assertEqual(submission.final_response_snapshot.content, "not-a-number")
        self.assertEqual(submission.grading_status, SubmissionEvent.GradingStatus.PENDING)
        self.assertIsNone(submission.score)
        self.assertIsNone(submission.is_correct)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}))
    def test_capture_v2_transition_survives_deadline_submission(self):
        from .capture_v2_services import ingest_capture_batch
        session = self._session_from(self.start())
        attempt = session.attempts.select_related("assignment__snapshot").get()
        stream, event_id = uuid.uuid4(), uuid.uuid4()
        before = ResponseState("single_choice", None)
        after = ResponseState("single_choice", "B")
        ingest_capture_batch(student=self.student, document={
            "client_instance_id": str(stream),
            "events": [{
                "event_id": str(event_id), "event_type": "response_transition",
                "event_version": 1, "session_id": session.pk, "attempt_id": attempt.pk,
                "client_sequence_no": 1, "client_wall_time_utc": timezone.now().isoformat(),
                "client_monotonic_ms": 100, "client_time_origin": "2026-09-06T00:00:00Z",
                "quality_flags": [], "app_version": "deadline-test",
                "payload": {
                    "capture_version": "capture-v2", "operation_id": str(uuid.uuid4()),
                    "branch_id": str(stream), "parent_event_id": None,
                    "before_state": before.state_json, "after_state": after.state_json,
                    "before_state_hash": before.state_hash, "after_state_hash": after.state_hash,
                    "input_type": "insertText", "source": "student",
                },
            }],
        })
        save_draft(attempt.pk, "ignored", target_event_id=str(event_id), expected_version=0)
        attempt.refresh_from_db()
        saved_snapshot_id = attempt.current_response_snapshot_id
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        attempt.refresh_from_db()
        self.assertEqual(attempt.final_response_event_id, event_id)
        self.assertEqual(attempt.final_response_snapshot_id, saved_snapshot_id)
        self.assertEqual(attempt.final_answer, "B")
        self.assertEqual(SubmissionEvent.objects.get(attempt=attempt).final_response_snapshot_id, saved_snapshot_id)

    def test_late_save_at_total_deadline_commits_only_the_saved_answer(self):
        session = self._session_from(self.start())
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "B")
        session.deadline_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["deadline_at"])
        with self.assertRaises(ValidationError):
            save_draft(attempt.pk, "A")
        attempt.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(attempt.final_answer, "B")
        self.assertEqual(session.status, session.Status.EXPIRED)
        self.assertEqual(session.submission_count, 1)

    def test_empty_response_snapshot_does_not_create_a_submission(self):
        session = self._session_from(self.start())
        attempt = session.attempts.get()
        snapshot = create_response_snapshot(
            attempt=attempt, content="", response_state=ResponseState("single_choice", None),
        )
        attempt.current_response_snapshot = snapshot
        attempt.save(update_fields=["current_response_snapshot"])
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.UNANSWERED)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_snapshot_answer_is_used_when_compatibility_draft_is_missing(self):
        session = self._session_from(self.start())
        attempt = session.attempts.get()
        snapshot = create_response_snapshot(
            attempt=attempt, content="B", response_state=ResponseState("single_choice", "B"),
        )
        attempt.current_response_snapshot = snapshot
        attempt.save(update_fields=["current_response_snapshot"])
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        submission = SubmissionEvent.objects.get(attempt=attempt)
        self.assertEqual(submission.final_answer, "B")
        self.assertEqual(submission.final_response_snapshot_id, snapshot.pk)
        self.assertTrue(submission.is_correct)

    def test_single_item_deadline_stays_timed_out(self):
        session = self._session_from(self.start())
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "B")
        attempt.current_effective_deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=["current_effective_deadline_at"])
        with self.assertRaises(ValidationError):
            save_draft(attempt.pk, "A")
        attempt.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.TIMED_OUT)
        self.assertEqual(session.status, session.Status.IN_PROGRESS)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_expiry_worker_submits_without_a_browser_request(self):
        session = self._session_from(self.start())
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, "B")
        session.deadline_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["deadline_at"])
        call_command("expire_due_sessions", stdout=StringIO())
        self.assertEqual(SubmissionEvent.objects.get(attempt=attempt).final_answer, "B")

    def test_expired_historical_session_is_not_modified(self):
        session = self._session_from(self.start())
        attempt = session.attempts.get()
        attempt.draft_answer = "B"
        attempt.save(update_fields=["draft_answer"])
        session.status = session.Status.EXPIRED
        session.terminal_at = timezone.now() - timedelta(days=1)
        session.save(update_fields=["status", "terminal_at"])
        terminal_at = session.terminal_at
        _terminate_for_deadline(session, now=timezone.now(), reason="deadline_expired")
        session.refresh_from_db()
        self.assertEqual(session.terminal_at, terminal_at)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_deadline_submits_a_saved_answer(self):
        started = self.start()
        session = self._session_from(started)
        assignment = session.assignments.get()
        attempt = record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, "B")

        _terminate_for_deadline(
            session,
            now=timezone.now() + timedelta(minutes=1),
            reason="deadline_expired",
        )

        attempt.refresh_from_db()
        session.refresh_from_db()
        submission = SubmissionEvent.objects.get(attempt=attempt)
        grade = GradeRecord.objects.get(submission=submission)

        self.assertEqual(session.status, session.Status.EXPIRED)
        self.assertEqual(session.submission_count, 1)
        self.assertEqual(attempt.status, Attempt.Status.SUBMITTED)
        self.assertEqual(attempt.final_answer, "B")
        self.assertEqual(submission.final_answer, "B")
        self.assertEqual(grade.status, GradeRecord.Status.COMPLETED)
        self.assertTrue(
            AssessmentEvent.objects.filter(
                attempt=attempt,
                event_type="response_committed",
            ).exists()
        )

    def test_deadline_leaves_empty_attempt_unanswered(self):
        started = self.start()
        session = self._session_from(started)
        assignment = session.assignments.get()
        attempt = record_visit(session.pk, assignment.pk)

        _terminate_for_deadline(
            session,
            now=timezone.now() + timedelta(minutes=1),
            reason="deadline_expired",
        )

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.UNANSWERED)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_deadline_auto_submission_is_idempotent(self):
        started = self.start()
        session = self._session_from(started)
        assignment = session.assignments.get()
        attempt = record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, "B")

        terminal_at = timezone.now() + timedelta(minutes=1)
        _terminate_for_deadline(session, now=terminal_at, reason="deadline_expired")
        first_submission_count = SubmissionEvent.objects.filter(attempt=attempt).count()
        first_event_count = AssessmentEvent.objects.filter(
            attempt=attempt,
            event_type="response_committed",
        ).count()
        self.assertEqual(first_submission_count, 1)
        self.assertEqual(first_event_count, 1)
        session.refresh_from_db()
        self.assertEqual(session.submission_count, 1)

        # A second expiry request must not create another formal submission.
        session.refresh_from_db()
        _terminate_for_deadline(session, now=terminal_at, reason="deadline_expired")

        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), first_submission_count)
        self.assertEqual(
            AssessmentEvent.objects.filter(
                attempt=attempt,
                event_type="response_committed",
            ).count(),
            first_event_count,
        )

    def test_deadline_marks_uncollected_confidence_without_probability(self):
        started = self.start()
        session = self._session_from(started)
        assignment = session.assignments.get()
        attempt = record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, "B")
        confidence = ConfidenceAssignment.objects.get(attempt=attempt)
        confidence.condition = ConfidenceAssignment.Condition.PROMPT
        confidence.assignment_unit = ConfidenceAssignment.AssignmentUnit.ATTEMPT
        confidence.assignment_version = "v1"
        confidence.response_status = ConfidenceAssignment.ResponseStatus.PROMPT_SHOWN
        confidence.save(
            update_fields=[
                "condition",
                "assignment_unit",
                "assignment_version",
                "response_status",
            ]
        )

        _terminate_for_deadline(
            session,
            now=timezone.now() + timedelta(minutes=1),
            reason="deadline_expired",
        )

        confidence.refresh_from_db()
        self.assertEqual(
            confidence.response_status,
            ConfidenceAssignment.ResponseStatus.SKIPPED,
        )
        self.assertIsNone(confidence.probability)
        event = AssessmentEvent.objects.get(
            attempt=attempt,
            event_type="confidence_skipped",
        )
        self.assertEqual(event.event_version, 2)
        self.assertEqual(event.payload["reason_code"], "deadline_expired")

    def _session_from(self, response):
        from .models import ExperimentSession

        return ExperimentSession.objects.get(pk=response.json()["session_id"])
