from django.test import TestCase

from apps.accounts.models import Course, User
from apps.assessment.models import Attempt, ExperimentSession, StudentQuestionAssignment
from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.question_bank.models import Question, QuestionBank

from .data_scope import DataScope
from .quality_reports import EvaluationState, build_quality_report


class QualityReportTests(TestCase):
    def test_legacy_attempt_without_native_events_is_not_evaluable_not_failed(self):
        teacher = User.objects.create_user(
            username="quality-teacher", role=User.Role.TEACHER
        )
        student = User.objects.create_user(
            username="quality-student", role=User.Role.STUDENT
        )
        course = Course.objects.create(
            owner=teacher, name="质量课程", code="QUALITY101", term="2026 秋"
        )
        bank = QuestionBank.objects.create(
            course=course, created_by=teacher, name="质量题库"
        )
        experiment = Experiment.objects.create(
            course=course,
            created_by=teacher,
            question_bank=bank,
            name="质量实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment, version_number=1, published_by=teacher
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="质量题",
            correct_answer="答案",
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=question,
            source_sequence=1,
            code="QUALITY-Q1",
            question_type=Question.Type.SHORT,
            text="质量题",
            correct_answer="答案",
            primary_kc="质量 KC",
            all_kcs=["质量 KC"],
        )
        session = ExperimentSession.objects.create(version=version, student=student)
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(session=session, assignment=assignment)

        report = build_quality_report(
            DataScope.from_mapping(
                course,
                {"version_mode": "single", "version_id": version.pk},
            )
        )
        state_chain = next(
            row
            for row in report.rows
            if row.attempt_id == attempt.pk and row.rule == "state_chain"
        )

        self.assertEqual(state_chain.state, EvaluationState.NOT_EVALUABLE)
