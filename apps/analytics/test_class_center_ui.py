import re
import uuid
from decimal import Decimal

from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    GradeRecord,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.experiments.presentation_instances import get_or_create_default_item_instance
from apps.experiments.services import publish_experiment, save_participant_rules
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept


@override_settings(CLASS_CENTER_ENABLED=True)
class ClassCenterUIContractTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="class-center-ui-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="班级中心界面课程",
            code="CLASS-CENTER-UI",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="界面实验班",
            academic_year="2026",
        )
        self.student = User.objects.create_user(
            username="UI-S001",
            student_number="UI-S001",
            display_name="界面学生",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="界面测试题库",
        )
        concept = Concept.objects.create(course=self.course, name="界面 KC")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="界面统计题",
            correct_answer="正确",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="界面诊断实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        save_participant_rules(self.experiment, [self.class_group], [], [])
        version = publish_experiment(self.experiment, self.teacher)
        class_snapshot = version.class_snapshots.get(
            source_class_group=self.class_group
        )
        question_snapshot = version.question_snapshots.get()
        session = ExperimentSession.objects.create(
            version=version,
            student=self.student,
            class_snapshot=class_snapshot,
            class_snapshot_name=class_snapshot.display_name,
            status=ExperimentSession.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=question_snapshot,
            item_instance=get_or_create_default_item_instance(question_snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(
            session=session,
            assignment=assignment,
            status=Attempt.Status.SUBMITTED,
            submitted_at=timezone.now(),
            first_to_submit_duration_ms=2100,
        )
        submission = SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer="正确",
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )
        GradeRecord.objects.create(
            submission=submission,
            version=1,
            status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE,
            is_correct=True,
            score=Decimal("1"),
            max_score=Decimal("1"),
        )
        self.url = reverse(
            "courses:class_detail",
            args=[self.course.pk, self.class_group.pk],
        )
        self.client.force_login(self.teacher)

    def test_overview_uses_accessible_fact_graphics_and_frozen_metric_language(self):
        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "总体正确率")
        self.assertNotContains(response, "平均正确率")
        self.assertContains(response, "统计生成时间")
        self.assertContains(response, "范围内包含进行中实验")
        self.assertContains(response, '<progress max="1"', html=False)
        self.assertContains(response, 'role="img"', html=False)
        self.assertContains(
            response,
            "<title>界面 KC正确率 100.0%</title>",
            html=False,
        )
        remote_assets = [
            target
            for target in re.findall(r'(?:href|src)="([^"]+)"', html)
            if target.startswith(("http://", "https://"))
        ]
        self.assertEqual(remote_assets, [])
        for forbidden in (
            "ExperimentVersion",
            "Session",
            "Attempt",
            "snapshot UUID",
            "raw payload",
            "alert(",
            "confirm(",
        ):
            self.assertNotIn(forbidden, html)

    def test_single_experiment_student_page_exposes_rank_and_phase_two_student_link(self):
        response = self.client.get(
            self.url,
            {
                "section": "students",
                "experiment_id": self.experiment.pk,
                "sort": "rank",
            },
        )
        html = response.content.decode()
        class_nav = re.search(
            r'<nav class="class-center-nav".*?</nav>',
            html,
            flags=re.DOTALL,
        ).group(0)

        self.assertEqual(class_nav.count('aria-current="page"'), 1)
        self.assertContains(response, "class-center-student-table", html=False)
        self.assertContains(response, "class-center-student-cards", html=False)
        self.assertContains(response, "当前实验排名")
        self.assertContains(response, "1 / 1")
        student_url = reverse(
            "courses:class_student_detail",
            args=[self.course.pk, self.class_group.pk, self.student.pk],
        )
        self.assertIn(student_url, html)
