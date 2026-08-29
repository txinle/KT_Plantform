import uuid
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from .models import AssessmentEvent, Attempt, SubmissionEvent
from .services import get_or_create_session, record_visit, save_draft, submit_attempt
from .event_services import append_assessment_event

class AssessmentFlowTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="S1", role=User.Role.STUDENT, student_number="S1")
        bank = QuestionBank.objects.create(owner=self.teacher, name="bank")
        concept = Concept.objects.create(owner=self.teacher, scope=Concept.Scope.PRIVATE, name="加法", normalized_name="加法")
        question = Question.objects.create(bank=bank, sequence=1, question_type=Question.Type.SINGLE, text="2+2", correct_answer="B")
        QuestionOption.objects.create(question=question, key="A", text="3")
        QuestionOption.objects.create(question=question, key="B", text="4")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(owner=self.teacher, question_bank=bank, name="exp")
        ExperimentQuestion.objects.create(experiment=self.experiment, question=question, display_order=1)
        ExperimentStudent.objects.create(experiment=self.experiment, student=self.student)
        publish_experiment(self.experiment, self.teacher)
        self.experiment.refresh_from_db()

    def test_visit_draft_submit_and_idempotency(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        attempt = record_visit(session.id, assignment.id)
        self.assertEqual(attempt.visit_count, 1)
        save_draft(attempt.id, "B")
        token = uuid.uuid4()
        submitted = submit_attempt(attempt.id, token)
        self.assertTrue(submitted.is_correct)
        self.assertEqual(submitted.submission_sequence_index, 1)
        repeated = submit_attempt(attempt.id, token)
        self.assertEqual(repeated.id, submitted.id)
        self.assertEqual(Attempt.objects.filter(status=Attempt.Status.SUBMITTED).count(), 1)
        committed = AssessmentEvent.objects.get(
            attempt=submitted,
            event_type="response_committed",
        )
        submission = SubmissionEvent.objects.get(attempt=submitted)
        self.assertEqual(committed.payload["submission_token"], str(submission.submission_token))
        self.assertEqual(committed.correlation_id, submission.submission_token)
        self.assertTrue(
            AssessmentEvent.objects.filter(
                attempt=submitted,
                event_type="attempt_closed",
                payload={"close_reason": "submitted"},
            ).exists()
        )
        with self.assertRaises(ValueError):
            append_assessment_event(
                event_type="response_committed",
                event_version=1,
                event_domain="assessment",
                actor_source="server",
                session=submitted.session,
                attempt=submitted,
                payload=committed.payload,
                correlation_id=submission.submission_token,
            )
        self.assertEqual(
            AssessmentEvent.objects.filter(
                attempt=submitted,
                event_type="response_committed",
            ).count(),
            1,
        )

    def test_committed_event_failure_rolls_back_the_business_submission(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        attempt = record_visit(session.id, assignment.id)
        save_draft(attempt.id, "B")

        with patch(
            "apps.assessment.services.append_assessment_event",
            side_effect=RuntimeError("event write failed"),
        ), self.assertRaises(RuntimeError):
            submit_attempt(attempt.id, uuid.uuid4())

        attempt.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.DRAFT)
        self.assertIsNone(attempt.final_answer)
        self.assertEqual(session.submission_count, 0)
        self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())
        self.assertFalse(
            AssessmentEvent.objects.filter(
                attempt=attempt,
                event_type__in=["response_committed", "attempt_closed"],
            ).exists()
        )

    def test_question_page_exposes_versioned_native_collector_contract(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        self.client.force_login(self.student)

        response = self.client.get(
            reverse("assessment:question", args=[session.pk, assignment.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-process-session-id="{session.pk}"',
        )
        self.assertContains(
            response,
            f'data-process-attempt-id="{assignment.attempt.pk}"',
        )
        self.assertContains(
            response,
            f'data-process-event-url="{reverse("assessment_process:event_batch")}"',
        )
        self.assertContains(
            response,
            f'data-time-sync-create-url="{reverse("assessment_process:time_sync_create")}"',
        )
        self.assertContains(response, 'data-heartbeat-interval-seconds="30"')
        content = response.content.decode("utf-8")
        self.assertLess(
            content.index("js/process-event-collector.js"),
            content.index("js/assessment.js"),
        )

    def test_returning_to_saved_draft_exposes_previous_response_state(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()
        attempt = record_visit(session.id, assignment.id)
        save_draft(attempt.id, "B")
        self.client.force_login(self.student)

        response = self.client.get(
            reverse("assessment:question", args=[session.pk, assignment.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="initial-response-state-data"')
        self.assertContains(response, '"B"')

    def test_native_collector_source_avoids_prohibited_continuous_capture(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "static"
            / "js"
            / "process-event-collector.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn("keydown", source)
        self.assertNotIn("keyup", source)
        self.assertNotIn("mousemove", source)
        self.assertNotIn("mouse_x", source)
        self.assertNotIn("mouse_y", source)
