import json

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentStudent
from apps.experiments.services import publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import allocate_course_question_identity

from .models import DraftSaveEvent, ExperimentSession


class StudentCourseNavigationTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(
            username="S1",
            student_number="S1",
            role=User.Role.STUDENT,
        )
        self.other_student = User.objects.create_user(
            username="S2",
            student_number="S2",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="高等数学",
            code="MATH101",
            term="2026春",
        )
        class_group = ClassGroup.objects.create(course=self.course, name="一班")
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="题库",
        )
        concept = Concept.objects.create(course=self.course, name="加法")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="1+1",
            correct_answer="2",
        )
        self.question_identity = allocate_course_question_identity(self.course, question)
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="测验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student,
            class_snapshot_name="一班",
        )
        publish_experiment(self.experiment, self.teacher)
        self.experiment.refresh_from_db()

    def test_student_selects_course_before_experiment(self):
        self.client.force_login(self.student)

        dashboard = self.client.get(reverse("assessment:dashboard"))
        self.assertContains(dashboard, "我的课程")
        self.assertContains(dashboard, "高等数学")
        self.assertNotContains(dashboard, "开始实验")

        course_page = self.client.get(reverse("assessment:course", args=[self.course.pk]))
        self.assertContains(course_page, "MATH101")
        self.assertContains(course_page, "测验")
        self.assertContains(course_page, "开始实验")

    def test_student_navigation_keeps_assigned_numbers_and_shows_the_stable_course_code(self):
        self.client.force_login(self.student)
        self.client.get(reverse("assessment:start", args=[self.experiment.pk]))
        session = ExperimentSession.objects.get(student=self.student)
        assignment = session.assignments.get()

        response = self.client.get(
            reverse("assessment:question", args=[session.pk, assignment.pk])
        )

        self.assertContains(response, "第 1 题")
        self.assertContains(response, self.question_identity.stable_code)

    def test_student_cannot_open_course_without_active_enrollment(self):
        self.client.force_login(self.other_student)

        response = self.client.get(reverse("assessment:course", args=[self.course.pk]))

        self.assertEqual(response.status_code, 404)

    def test_archived_course_cannot_create_new_session(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.student)

        response = self.client.get(reverse("assessment:start", args=[self.experiment.pk]))

        self.assertRedirects(response, reverse("assessment:dashboard"))
        self.assertFalse(ExperimentSession.objects.exists())

    def test_experiment_assignment_without_course_enrollment_cannot_start(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.other_student,
            class_snapshot_name="错误分配",
        )
        self.client.force_login(self.other_student)

        response = self.client.get(reverse("assessment:start", args=[self.experiment.pk]))

        self.assertRedirects(response, reverse("assessment:dashboard"))
        self.assertFalse(ExperimentSession.objects.filter(student=self.other_student).exists())

    def test_archiving_course_blocks_writes_to_existing_session(self):
        self.client.force_login(self.student)
        self.client.get(reverse("assessment:start", args=[self.experiment.pk]))
        session = ExperimentSession.objects.get(student=self.student)
        attempt = session.attempts.get()
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])

        response = self.client.post(
            reverse("assessment:save_draft", args=[attempt.pk]),
            data=json.dumps({"answer": "2"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.draft_answer)
        self.assertFalse(DraftSaveEvent.objects.exists())

    def test_inactive_enrollment_blocks_writes_to_existing_session(self):
        self.client.force_login(self.student)
        self.client.get(reverse("assessment:start", args=[self.experiment.pk]))
        session = ExperimentSession.objects.get(student=self.student)
        attempt = session.attempts.get()
        CourseEnrollment.objects.filter(course=self.course, student=self.student).update(is_active=False)

        response = self.client.post(
            reverse("assessment:save_draft", args=[attempt.pk]),
            data=json.dumps({"answer": "2"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(DraftSaveEvent.objects.exists())
