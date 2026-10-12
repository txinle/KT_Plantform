from apps.api.test_support import domain_payload
from datetime import timedelta
from io import StringIO
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections, transaction
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
    finish_session,
    mark_skipped,
    record_visit,
    save_draft,
    submit_attempt,
)
from .response_snapshot_services import ResponseState, create_response_snapshot
from .test_student_api import StudentApiFixtureMixin


class CaptureWriteIsolationTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_two_students_can_finish_concurrently_without_deadlock(self):
        from .services import get_or_create_session
        from .event_services import _register_schema_artifact
        _register_schema_artifact('session_completed', 1)
        sessions = [get_or_create_session(self.experiment, student, class_task=self.task)
                    for student in (self.student, self.peer)]
        ready = Barrier(2)

        def finish(session):
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                ready.wait(timeout=5)
                return finish_session(session.pk).status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish, sessions))
        self.assertEqual(results, [sessions[0].Status.COMPLETED] * 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.result_revision, 0)
        for session in sessions:
            session.refresh_from_db()
            self.assertEqual(session.result_revision, 1)
        self.assertEqual(AssessmentEvent.objects.filter(session__in=sessions, event_type='session_completed').count(), 2)

    def test_peer_submission_can_commit_during_first_students_research_revision_write(self):
        from .services import get_or_create_session, _schedule_research_materialization
        from .event_services import _register_schema_artifact

        for event_type in ('response_committed', 'attempt_closed'):
            _register_schema_artifact(event_type, 1)
        first = get_or_create_session(self.experiment, self.student, class_task=self.task)
        peer = get_or_create_session(self.experiment, self.peer, class_task=self.task)
        attempts = [record_visit(s.pk, s.assignments.get().pk) for s in (first, peer)]
        for attempt in attempts:
            save_draft(attempt.pk, 'B')
        independent_write = Event()
        release = Event()

        def schedule(session_id):
            if session_id == first.pk:
                independent_write.set()
                if not release.wait(timeout=10):
                    raise TimeoutError('test transaction was not released')
            return _schedule_research_materialization(session_id)

        def submit(attempt):
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '750ms'")
                return submit_attempt(attempt.pk, uuid.uuid4()).pk
            finally:
                connections.close_all()

        with patch('apps.assessment.services._schedule_research_materialization', schedule):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(submit, attempts[0])
                try:
                    self.assertTrue(independent_write.wait(timeout=5))
                    self.assertEqual(pool.submit(submit, attempts[1]).result(timeout=5), attempts[1].pk)
                finally:
                    release.set()
                self.assertEqual(first_future.result(timeout=10), attempts[0].pk)
        self.task.refresh_from_db()
        self.assertEqual(self.task.result_revision, 0)
        self.assertEqual(SubmissionEvent.objects.filter(attempt__in=attempts).count(), 2)

    def test_submission_failure_rolls_back_facts_and_both_revisions(self):
        from .services import get_or_create_session
        session = get_or_create_session(self.experiment, self.student, class_task=self.task)
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, 'B')
        revisions = (session.result_revision, session.research_source_revision, self.task.result_revision)
        with patch('apps.assessment.services._schedule_research_materialization', side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                submit_attempt(attempt.pk, uuid.uuid4())
        attempt.refresh_from_db()
        session.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.DRAFT)
        self.assertEqual((session.result_revision, session.research_source_revision, self.task.result_revision), revisions)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertFalse(GradeRecord.objects.filter(submission__attempt=attempt).exists())
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt, event_type='response_committed').exists())

    def other_student_can_write_while_first_save_is_uncommitted(self, *, submit, visit=False, peer_visit=False):
        from .services import get_or_create_session

        first = get_or_create_session(self.experiment, self.student, class_task=self.task)
        peer = get_or_create_session(self.experiment, self.peer, class_task=self.task)
        first_attempt = record_visit(first.pk, first.assignments.get().pk)
        peer_attempt = record_visit(peer.pk, peer.assignments.get().pk)
        save_draft(peer_attempt.pk, 'B')

        def write_peer():
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '750ms'")
                if submit:
                    return submit_attempt(peer_attempt.pk, uuid.uuid4()).pk
                if peer_visit:
                    return record_visit(peer.pk, peer_attempt.assignment_id).pk
                return save_draft(peer_attempt.pk, 'B').pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                if visit:
                    record_visit(first.pk, first_attempt.assignment_id)
                else:
                    save_draft(first_attempt.pk, 'B')
                written = pool.submit(write_peer).result(timeout=5)
                self.assertTrue(written)
        peer_attempt.refresh_from_db()
        self.assertEqual(peer_attempt.status, Attempt.Status.SUBMITTED if submit else Attempt.Status.DRAFT)
        self.assertEqual(peer_attempt.draft_answer, 'B')

    def test_other_student_save_does_not_wait_for_shared_definition_or_question(self):
        self.other_student_can_write_while_first_save_is_uncommitted(submit=False)

    def test_other_student_submit_does_not_wait_for_another_students_save(self):
        self.other_student_can_write_while_first_save_is_uncommitted(submit=True)

    def test_other_student_question_entry_does_not_wait_for_another_students_entry(self):
        self.other_student_can_write_while_first_save_is_uncommitted(submit=False, visit=True, peer_visit=True)

    def other_student_submission_does_not_deadlock_with_terminal_write(self, *, expired=False, attempt_timeout=False):
        from .services import get_or_create_session
        from .event_services import _register_schema_artifact

        # Match the school retest, where the preceding small cohort has already
        # registered these immutable schemas outside the competing transactions.
        for event_type in ('response_committed', 'attempt_closed', 'session_completed', 'session_expired'):
            _register_schema_artifact(event_type, 1)

        first = get_or_create_session(self.experiment, self.student, class_task=self.task)
        peer = get_or_create_session(self.experiment, self.peer, class_task=self.task)
        first_attempt = record_visit(first.pk, first.assignments.get().pk)
        peer_attempt = record_visit(peer.pk, peer.assignments.get().pk)
        save_draft(first_attempt.pk, 'B')
        save_draft(peer_attempt.pk, 'B')
        finish_reached_revision = Event()

        def finish_first():
            connections.close_all()
            def observe_revision(execute, sql, params, many, context):
                if sql.startswith('UPDATE "assessment_experimentsession"') and '"result_revision"' in sql:
                    finish_reached_revision.set()
                return execute(sql, params, many, context)
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET statement_timeout = '10s'")
                with connections['default'].execute_wrapper(observe_revision):
                    if expired:
                        _terminate_for_deadline(first, now=timezone.now(), reason='deadline_expired', attempt=first_attempt if attempt_timeout else None)
                        return first.pk
                    return finish_session(first.pk).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                # An independent eligibility update must no longer serialize
                # terminal writes and submissions through the class revision.
                type(self.task).objects.filter(pk=self.task.pk).bump_result_revision()
                future = pool.submit(finish_first)
                self.assertTrue(finish_reached_revision.wait(timeout=5))
                submitted_attempt = submit_attempt(peer_attempt.pk, uuid.uuid4())
            self.assertEqual(future.result(timeout=15), first.pk)
        first.refresh_from_db()
        peer_attempt.refresh_from_db()
        self.assertEqual(first.status, first.Status.IN_PROGRESS if attempt_timeout else first.Status.EXPIRED if expired else first.Status.COMPLETED)
        self.assertEqual(peer_attempt.status, Attempt.Status.SUBMITTED)
        self.assertEqual(SubmissionEvent.objects.get(attempt=peer_attempt).attempt_id, submitted_attempt.pk)

    def test_student_finish_does_not_deadlock_with_another_students_submission_commit(self):
        self.other_student_submission_does_not_deadlock_with_terminal_write()

    def test_student_expiry_does_not_deadlock_with_another_students_submission_commit(self):
        self.other_student_submission_does_not_deadlock_with_terminal_write(expired=True)

    def test_attempt_timeout_does_not_deadlock_with_another_students_submission_commit(self):
        self.other_student_submission_does_not_deadlock_with_terminal_write(expired=True, attempt_timeout=True)

    def test_students_can_skip_without_locking_the_shared_definition(self):
        from .services import get_or_create_session

        first = get_or_create_session(self.experiment, self.student, class_task=self.task)
        peer = get_or_create_session(self.experiment, self.peer, class_task=self.task)
        first_attempt = record_visit(first.pk, first.assignments.get().pk)
        peer_attempt = record_visit(peer.pk, peer.assignments.get().pk)

        def skip_peer():
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '750ms'")
                return mark_skipped(peer_attempt.pk).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                mark_skipped(first_attempt.pk)
                self.assertEqual(pool.submit(skip_peer).result(timeout=5), peer_attempt.pk)
        for attempt in (first_attempt, peer_attempt):
            attempt.refresh_from_db()
            self.assertEqual(attempt.status, Attempt.Status.SKIPPED)


class DeadlineAutoSubmissionTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_scheduler_does_not_lock_a_session_with_future_deadlines(self):
        session = self._session_from(self.start())
        locked = []

        def inspect(execute, sql, params, many, context):
            if 'FOR UPDATE' in sql and 'assessment_experimentsession' in sql:
                locked.append(sql)
            return execute(sql, params, many, context)

        with connections['default'].execute_wrapper(inspect):
            call_command('expire_due_sessions', stdout=StringIO())
        self.assertEqual(locked, [])
        session.refresh_from_db()
        self.assertEqual(session.status, session.Status.IN_PROGRESS)

    def test_scheduler_skips_busy_due_session_then_settles_it_next_scan(self):
        session = self._session_from(self.start())
        attempt = record_visit(session.pk, session.assignments.get().pk)
        save_draft(attempt.pk, 'B')
        session.deadline_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=['deadline_at'])

        def scan():
            connections.close_all()
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '750ms'")
                output = StringIO()
                call_command('expire_due_sessions', stdout=output)
                return output.getvalue().strip()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                type(session).objects.select_for_update().get(pk=session.pk)
                self.assertEqual(pool.submit(scan).result(timeout=5), '0')
        call_command('expire_due_sessions', stdout=StringIO())
        session.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(session.status, session.Status.EXPIRED)
        self.assertEqual(SubmissionEvent.objects.get(attempt=attempt).final_answer, 'B')
        self.assertEqual(session.result_revision, 2)
        self.assertEqual(self.task.result_revision, 0)
        call_command('expire_due_sessions', stdout=StringIO())
        self.assertEqual(SubmissionEvent.objects.filter(attempt=attempt).count(), 1)

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

        return ExperimentSession.objects.get(pk=domain_payload(response)["session_id"])
