from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentStudent
from apps.question_bank.models import QuestionBank


class CourseWorkspaceScopeTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.course_a = Course.objects.create(
            owner=self.teacher,
            name="课程 A",
            code="A",
            term="2026春",
        )
        self.course_b = Course.objects.create(
            owner=self.teacher,
            name="课程 B",
            code="B",
            term="2026春",
        )
        self.class_a = ClassGroup.objects.create(course=self.course_a, name="一班")
        self.class_b = ClassGroup.objects.create(course=self.course_b, name="一班")
        self.student_a = User.objects.create_user(
            username="SA",
            student_number="SA",
            display_name="甲",
            role=User.Role.STUDENT,
        )
        self.student_b = User.objects.create_user(
            username="SB",
            student_number="SB",
            display_name="乙",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course_a,
            class_group=self.class_a,
            student=self.student_a,
        )
        CourseEnrollment.objects.create(
            course=self.course_b,
            class_group=self.class_b,
            student=self.student_b,
        )
        self.bank_a = QuestionBank.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            name="A 题库",
        )
        self.bank_b = QuestionBank.objects.create(
            course=self.course_b,
            created_by=self.teacher,
            name="B 题库",
        )
        self.client.force_login(self.teacher)

    def test_bank_detail_and_class_detail_cannot_cross_course_boundary(self):
        bank_url = reverse("courses:bank_detail", args=[self.course_a.pk, self.bank_b.pk])
        class_url = reverse("courses:class_detail", args=[self.course_a.pk, self.class_b.pk])

        self.assertEqual(self.client.get(bank_url).status_code, 404)
        self.assertEqual(self.client.get(class_url).status_code, 404)

    def test_legacy_experiment_create_is_read_only(self):
        response = self.client.post(
            reverse("courses:experiment_create", args=[self.course_a.pk]),
            {
                "name": "越界实验",
                "description": "",
                "question_bank": self.bank_b.pk,
                "ordering_mode": "fixed",
                "feedback_mode": "none",
                "time_mode": "none",
                "resume_mode": "resumable",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "请使用新版实验设计器", status_code=409)
        self.assertFalse(Experiment.objects.filter(name="越界实验").exists())

    def test_experiment_assignment_rejects_student_from_another_course(self):
        experiment = Experiment.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            question_bank=self.bank_a,
            name="A 实验",
        )
        response = self.client.post(
            reverse("courses:experiment_assign", args=[self.course_a.pk, experiment.pk]),
            {"students": [self.student_b.pk]},
        )

        self.assertRedirects(
            response,
            reverse("courses:experiment_detail", args=[self.course_a.pk, experiment.pk]),
        )
        self.assertFalse(ExperimentStudent.objects.filter(experiment=experiment).exists())

    def test_legacy_experiment_edit_is_read_only(self):
        experiment = Experiment.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            question_bank=self.bank_a,
            name="A 实验",
        )
        url = reverse("courses:experiment_edit", args=[self.course_a.pk, experiment.pk])
        response = self.client.post(
            url,
            {
                "name": "越界修改",
                "description": "",
                "question_bank": self.bank_b.pk,
                "ordering_mode": "fixed",
                "feedback_mode": "none",
                "time_mode": "none",
                "resume_mode": "resumable",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "请在新版实验设计器中显式转换", status_code=409)
        experiment.refresh_from_db()
        self.assertEqual(experiment.question_bank, self.bank_a)
