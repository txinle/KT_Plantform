import uuid

from django.test import TestCase

from apps.accounts.models import User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import Attempt, GradeRecord, Submission, SubmissionEvent
from .services import (
    grade_submission,
    get_or_create_session,
    record_visit,
    save_draft,
    submit_attempt,
)
from .event_services import append_assessment_event


class SubmissionGradeContractTests(TestCase):
    def setUp(self):
        teacher = User.objects.create_user(username="b1-teacher", role=User.Role.TEACHER)
        student = User.objects.create_user(
            username="b1-student", role=User.Role.STUDENT, student_number="B1-1"
        )
        bank = QuestionBank.objects.create(owner=teacher, name="b1-bank")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="q",
            correct_answer="a",
        )
        concept = Concept.objects.create(
            owner=teacher,
            scope=Concept.Scope.PRIVATE,
            name="b1-kc",
            normalized_name="b1-kc",
        )
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        experiment = Experiment.objects.create(owner=teacher, question_bank=bank, name="b1-exp")
        ExperimentQuestion.objects.create(experiment=experiment, question=question, display_order=1)
        ExperimentStudent.objects.create(experiment=experiment, student=student)
        publish_experiment(experiment, teacher)
        session = get_or_create_session(Experiment.objects.get(pk=experiment.pk), student)
        assignment = session.assignments.get()
        self.attempt = record_visit(session.pk, assignment.pk)

    def test_draft_snapshot_pointer_is_persisted_and_restored(self):
        attempt = save_draft(self.attempt.pk, "draft")
        snapshot_id = attempt.current_response_snapshot_id

        self.assertIsNotNone(snapshot_id)
        restored = Attempt.objects.get(pk=attempt.pk)
        self.assertEqual(restored.current_response_snapshot_id, snapshot_id)

    def test_submission_is_canonical_alias_with_grading_lifecycle(self):
        save_draft(self.attempt.pk, "a")
        submitted = submit_attempt(self.attempt.pk, uuid.uuid4())
        submission = Submission.objects.get(attempt=submitted)

        self.assertIs(submission.__class__, SubmissionEvent)
        self.assertIn(submission.grading_status, {"pending", "completed", "regrade"})
        self.assertEqual(submission.final_response_snapshot_id, submitted.final_response_snapshot_id)

    def test_grading_creates_versioned_record_and_projects_attempt(self):
        save_draft(self.attempt.pk, "a")
        submitted = submit_attempt(self.attempt.pk, uuid.uuid4())
        submission = Submission.objects.get(attempt=submitted)

        record = grade_submission(submission.pk, score="1.00", is_correct=True)

        self.assertEqual(record.version, 2)
        self.assertEqual(record.status, GradeRecord.Status.COMPLETED)
        submitted.refresh_from_db()
        self.assertEqual(str(submitted.score), "1.00")
        self.assertTrue(submitted.is_correct)

    def test_grading_status_supports_pending_and_regrade_versions(self):
        save_draft(self.attempt.pk, "a")
        submitted = submit_attempt(self.attempt.pk, uuid.uuid4())
        submission = Submission.objects.get(attempt=submitted)

        pending = grade_submission(submission.pk, status="pending")
        self.assertEqual(pending.status, GradeRecord.Status.PENDING)
        self.assertEqual(submission.__class__.objects.get(pk=submission.pk).grading_status, "pending")

        regrade = grade_submission(submission.pk, status="regrade")
        self.assertEqual(regrade.status, GradeRecord.Status.REGRADE)
        self.assertEqual(regrade.supersedes_grade_id, pending.pk)

    def test_pending_commit_event_does_not_require_grade_values(self):
        save_draft(self.attempt.pk, "a")
        token = uuid.uuid4()
        now = __import__("django.utils.timezone", fromlist=["now"]).now()
        submission = SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=token,
            grading_status="pending",
            server_received_at=now,
            committed_at=now,
        )
        event = append_assessment_event(
            event_type="response_committed",
            event_version=1,
            event_domain="assessment",
            actor_source="server",
            session=self.attempt.session,
            assignment=self.attempt.assignment,
            attempt=self.attempt,
            question_snapshot=self.attempt.assignment.snapshot,
            item_instance=self.attempt.assignment.item_instance,
            correlation_id=token,
            payload={"submission_token": str(token), "grading_status": "pending"},
            capture_mode="native",
            app_version="server",
        )
        self.assertEqual(event.attempt_id, self.attempt.pk)
