import uuid
from unittest.mock import patch

from django.test import TestCase
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
        self.assertFalse(AssessmentEvent.objects.filter(attempt=attempt).exists())
