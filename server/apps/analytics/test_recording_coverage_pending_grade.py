import uuid

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.assessment.recording_capture_services import ensure_capture_contract_v1
from apps.assessment.response_snapshot_services import create_response_snapshot
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .recording_coverage import generate_session_coverage


class RecordingCoveragePendingGradeTests(TestCase):
    def setUp(self):
        teacher = User.objects.create_user(
            username="coverage-pending-grade-teacher", role=User.Role.TEACHER
        )
        student = User.objects.create_user(
            username="COVERAGE-PENDING-GRADE-S1",
            student_number="COVERAGE-PENDING-GRADE-S1",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=teacher, name="待评分 Coverage 题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="待评分 Coverage 题目",
            correct_answer="A",
        )
        experiment = Experiment.objects.create(
            owner=teacher, question_bank=bank, name="待评分 Coverage 实验"
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment, version_number=1, published_by=teacher
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="待评分 Coverage 题目",
            options=[{"key": "A", "text": "A"}, {"key": "B", "text": "B"}],
            correct_answer="A",
            primary_kc="KC",
            all_kcs=["KC"],
        )
        item_instance = get_or_create_default_item_instance(snapshot)
        session = ExperimentSession.objects.create(
            version=version,
            student=student,
            capture_contract=ensure_capture_contract_v1(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=item_instance,
            assigned_sequence_index=1,
        )
        self.session = session
        self.attempt = Attempt.objects.create(session=session, assignment=assignment)

    def _submission(self, *, snapshot_answer, final_answer, grading_status):
        response_snapshot = create_response_snapshot(
            attempt=self.attempt, content=snapshot_answer
        )
        self.attempt.final_response_snapshot = response_snapshot
        self.attempt.save(update_fields=["final_response_snapshot"])
        return SubmissionEvent.objects.create(
            attempt=self.attempt,
            submission_token=uuid.uuid4(),
            final_answer=final_answer,
            final_response_snapshot=response_snapshot,
            grading_status=grading_status,
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )

    def test_pending_grade_is_unusable_without_claiming_submission_chain_mismatch(self):
        submission = self._submission(
            snapshot_answer="A",
            final_answer="A",
            grading_status=SubmissionEvent.GradingStatus.PENDING,
        )
        GradeRecord.objects.create(
            submission=submission,
            status=GradeRecord.Status.PENDING,
            validity_status=GradeRecord.ValidityStatus.CANDIDATE,
        )

        run = generate_session_coverage(self.session, force=True)
        outcome = run.attempt_results.get(
            attempt=self.attempt
        ).analysis_eligibility["outcome"]

        self.assertFalse(outcome["usable"])
        self.assertEqual(outcome["status"], "insufficient")
        self.assertEqual(
            set(outcome["missing_reasons"]),
            {"grading_pending", "effective_grade_unavailable"},
        )
        self.assertNotIn("submission_chain_mismatch", outcome["missing_reasons"])

    def test_authoritative_snapshot_mismatch_remains_an_invalid_outcome(self):
        submission = self._submission(
            snapshot_answer="A",
            final_answer="B",
            grading_status=SubmissionEvent.GradingStatus.COMPLETED,
        )
        GradeRecord.objects.create(
            submission=submission,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
        )

        run = generate_session_coverage(self.session, force=True)
        outcome = run.attempt_results.get(
            attempt=self.attempt
        ).analysis_eligibility["outcome"]

        self.assertFalse(outcome["usable"])
        self.assertEqual(outcome["status"], "invalid")
        self.assertIn("final_snapshot_mismatch", outcome["missing_reasons"])
