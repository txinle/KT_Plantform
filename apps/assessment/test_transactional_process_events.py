import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)

from .models import (
    AssessmentEvent,
    Attempt,
    ExperimentSession,
    QuestionVisitEvent,
    SubmissionEvent,
)
from .services import (
    finish_session,
    get_or_create_session,
    record_visit,
    record_visit_entry,
    save_draft,
    submit_attempt,
)
from .test_support import accept_research_consent


class TransactionalProcessEventTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="transaction-event-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="TRANSACTION-EVENT-S1",
            student_number="TRANSACTION-EVENT-S1",
            role=User.Role.STUDENT,
        )
        accept_research_consent(self.student)
        bank = QuestionBank.objects.create(owner=self.teacher, name="事务事件题库")
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="事务事件 KC",
            normalized_name="transaction event kc",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="事务事件实验",
        )
        for sequence in range(1, 3):
            question = Question.objects.create(
                bank=bank,
                sequence=sequence,
                question_type=Question.Type.SINGLE,
                text=f"事务题目 {sequence}",
                correct_answer="A",
            )
            QuestionOption.objects.create(question=question, key="A", text="正确")
            QuestionOption.objects.create(question=question, key="B", text="错误")
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
            ExperimentQuestion.objects.create(
                experiment=self.experiment,
                question=question,
                display_order=sequence,
            )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student,
        )
        publish_experiment(self.experiment, self.teacher)
        self.experiment.refresh_from_db()

    def test_session_creation_and_item_visit_append_authoritative_lifecycle_events(self):
        session = get_or_create_session(self.experiment, self.student)
        repeated = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.order_by("assigned_sequence_index").first()
        attempt = record_visit(session.pk, assignment.pk)

        self.assertEqual(repeated.pk, session.pk)
        self.assertEqual(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="session_started",
            ).count(),
            1,
        )
        entered = AssessmentEvent.objects.get(
            attempt=attempt,
            event_type="item_entered",
        )
        self.assertEqual(entered.actor_source, "server")
        self.assertEqual(entered.payload["entry_reason"], "navigation_click")
        self.assertIsNone(entered.payload["from_assignment_id"])

    def test_navigation_entry_replay_refresh_and_true_return_preserve_visit_semantics(self):
        session = get_or_create_session(self.experiment, self.student)
        first, second = session.assignments.order_by("assigned_sequence_index")
        clicked_at = timezone.now() - timedelta(seconds=2)
        time_origin = clicked_at - timedelta(seconds=10)
        navigation_id = uuid.uuid4()

        entered = record_visit_entry(
            session.pk,
            first.pk,
            navigation_id=navigation_id,
            client_time=clicked_at,
            client_monotonic_ms="10000.125",
            client_time_origin=time_origin,
        )
        replayed = record_visit_entry(
            session.pk,
            first.pk,
            navigation_id=navigation_id,
            client_time=clicked_at,
            client_monotonic_ms="10000.125",
            client_time_origin=time_origin,
        )
        refreshed = record_visit_entry(session.pk, first.pk, entry_reason="resume")

        self.assertEqual(replayed.visit.pk, entered.visit.pk)
        self.assertEqual(refreshed.visit.pk, entered.visit.pk)
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), 1)
        self.assertEqual(entered.visit.client_occurred_at, clicked_at)
        self.assertEqual(entered.entry_event.correlation_id, navigation_id)
        self.assertEqual(entered.entry_event.client_wall_time_utc, clicked_at)
        self.assertEqual(str(entered.entry_event.client_monotonic_ms), "10000.125")
        self.assertEqual(entered.entry_event.client_time_origin, time_origin)
        self.assertEqual(entered.entry_event.payload["visit_id"], entered.visit.pk)

        record_visit_entry(session.pk, second.pk, navigation_id=uuid.uuid4())
        returned = record_visit_entry(session.pk, first.pk, navigation_id=uuid.uuid4())
        first.attempt.refresh_from_db()

        self.assertEqual(first.attempt.visit_count, 2)
        self.assertEqual(returned.visit.visit_index, 2)
        self.assertNotEqual(returned.visit.pk, entered.visit.pk)

    def test_return_to_submitted_question_increments_visit_without_rewriting_open_times(self):
        session = get_or_create_session(self.experiment, self.student)
        first, second = session.assignments.order_by("assigned_sequence_index")
        initial = record_visit_entry(session.pk, first.pk, navigation_id=uuid.uuid4())
        save_draft(first.attempt.pk, "A")
        submit_attempt(first.attempt.pk, uuid.uuid4())
        first.attempt.refresh_from_db()
        first_opened_at = first.attempt.first_opened_at
        last_opened_at = first.attempt.last_opened_at

        record_visit_entry(session.pk, second.pk, navigation_id=uuid.uuid4())
        returned = record_visit_entry(session.pk, first.pk, navigation_id=uuid.uuid4())
        first.attempt.refresh_from_db()

        self.assertEqual(initial.visit.visit_index, 1)
        self.assertEqual(returned.visit.visit_index, 2)
        self.assertEqual(first.attempt.visit_count, 2)
        self.assertEqual(first.attempt.first_opened_at, first_opened_at)
        self.assertEqual(first.attempt.last_opened_at, last_opened_at)

    def test_finish_closes_open_attempts_and_session_in_one_transaction(self):
        session = get_or_create_session(self.experiment, self.student)
        first = session.assignments.order_by("assigned_sequence_index").first()
        record_visit(session.pk, first.pk)

        finished = finish_session(session.pk)

        self.assertEqual(finished.status, ExperimentSession.Status.COMPLETED)
        self.assertEqual(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="session_completed",
            ).count(),
            1,
        )
        closed_attempt_ids = set(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="attempt_closed",
                payload={"close_reason": "unanswered"},
            ).values_list("attempt_id", flat=True)
        )
        self.assertEqual(
            closed_attempt_ids,
            set(session.attempts.values_list("pk", flat=True)),
        )

    def test_lifecycle_event_failure_rolls_back_business_transition(self):
        session = get_or_create_session(self.experiment, self.student)

        with patch(
            "apps.assessment.services.append_assessment_event",
            side_effect=RuntimeError("event unavailable"),
        ), self.assertRaises(RuntimeError):
            finish_session(session.pk)

        session.refresh_from_db()
        self.assertEqual(session.status, ExperimentSession.Status.IN_PROGRESS)
        self.assertFalse(
            session.attempts.exclude(status=Attempt.Status.UNVISITED).exists()
        )

    def test_invalid_answer_records_typed_rejection_without_submission(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.order_by("assigned_sequence_index").first()
        record_visit(session.pk, assignment.pk)
        token = uuid.uuid4()
        self.client.force_login(self.student)

        response = self.client.post(
            reverse("assessment:submit", args=[assignment.attempt.pk]),
            data=json.dumps({"submission_token": str(token)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        rejected = AssessmentEvent.objects.get(
            attempt=assignment.attempt,
            event_type="response_commit_rejected",
        )
        self.assertEqual(rejected.payload["submission_token"], str(token))
        self.assertEqual(rejected.payload["reason_code"], "invalid_answer")
        self.assertEqual(rejected.correlation_id, token)
        self.assertFalse(
            SubmissionEvent.objects.filter(attempt=assignment.attempt).exists()
        )
        self.assertFalse(
            AssessmentEvent.objects.filter(
                attempt=assignment.attempt,
                event_type="response_committed",
            ).exists()
        )

    def test_service_rejection_is_a_typed_validation_error(self):
        from .services import SubmissionValidationError, submit_attempt

        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.order_by("assigned_sequence_index").first()

        with self.assertRaises(SubmissionValidationError) as raised:
            submit_attempt(assignment.attempt.pk, uuid.uuid4())

        self.assertIsInstance(raised.exception, ValidationError)
        self.assertEqual(raised.exception.reason_code, "invalid_answer")

    def test_total_time_expiry_persists_terminal_projection_and_events(self):
        session = get_or_create_session(self.experiment, self.student)
        session.deadline_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["deadline_at"])
        assignment = session.assignments.order_by("assigned_sequence_index").first()

        with self.assertRaises(ValidationError):
            record_visit(session.pk, assignment.pk)

        session.refresh_from_db()
        self.assertEqual(session.status, ExperimentSession.Status.EXPIRED)
        self.assertEqual(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="session_expired",
            ).count(),
            1,
        )
        self.assertFalse(
            session.attempts.exclude(status=Attempt.Status.UNANSWERED).exists()
        )
        self.assertEqual(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="attempt_closed",
                payload={"close_reason": "unanswered"},
            ).count(),
            session.attempts.count(),
        )

    def test_per_question_timeout_persists_attempt_close_before_error(self):
        session = get_or_create_session(self.experiment, self.student)
        configuration = session.version.configuration
        configuration.update(
            {
                "time_mode": "per_question",
                "per_question_time_limit_seconds": 10,
            }
        )
        session.version.configuration = configuration
        session.version.save(update_fields=["configuration"])
        assignment = session.assignments.order_by("assigned_sequence_index").first()
        attempt = record_visit(session.pk, assignment.pk)
        attempt.active_seconds = 10
        attempt.last_opened_at = None
        attempt.save(update_fields=["active_seconds", "last_opened_at"])

        with self.assertRaises(ValidationError):
            save_draft(attempt.pk, "A")

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.TIMED_OUT)
        closed = AssessmentEvent.objects.get(
            attempt=attempt,
            event_type="attempt_closed",
        )
        self.assertEqual(closed.payload["close_reason"], "timed_out")

    def test_session_invalidation_is_authoritative_and_idempotent(self):
        from .services import invalidate_session

        session = get_or_create_session(self.experiment, self.student)
        first = invalidate_session(session.pk, reason_code="research_withdrawal")
        repeated = invalidate_session(session.pk, reason_code="research_withdrawal")

        self.assertEqual(first.status, ExperimentSession.Status.INVALIDATED)
        self.assertEqual(repeated.status, ExperimentSession.Status.INVALIDATED)
        terminal = AssessmentEvent.objects.get(
            session=session,
            event_type="session_invalidated",
        )
        self.assertEqual(terminal.payload["reason_code"], "research_withdrawal")
        self.assertEqual(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="session_invalidated",
            ).count(),
            1,
        )
        self.assertEqual(
            AssessmentEvent.objects.filter(
                session=session,
                event_type="attempt_closed",
                payload={"close_reason": "invalidated"},
            ).count(),
            session.attempts.count(),
        )

    def test_response_event_failure_rolls_back_submission_projection(self):
        from .services import submit_attempt

        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.order_by("assigned_sequence_index").first()
        attempt = record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, "A")

        with patch(
            "apps.assessment.services.append_assessment_event",
            side_effect=RuntimeError("event unavailable"),
        ), self.assertRaises(RuntimeError):
            submit_attempt(attempt.pk, uuid.uuid4())

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.DRAFT)
        self.assertIsNone(attempt.submission_token)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
