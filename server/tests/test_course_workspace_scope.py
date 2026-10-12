from django.test import TestCase
from django.urls import reverse
from apps.api.test_support import domain_payload

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
        bank_url = f"/api/v1/teacher/courses/{self.course_a.pk}/banks/{self.bank_b.pk}/"
        class_url = f"/api/v1/teacher/accounts/courses/{self.course_a.pk}/classes/{self.class_b.pk}/manage/"

        self.assertEqual(self.client.get(f"/api/v1/teacher/courses/{self.course_a.pk}/banks/{self.bank_a.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/teacher/accounts/courses/{self.course_a.pk}/classes/{self.class_a.pk}/manage/").status_code, 200)

        self.assertEqual(self.client.get(bank_url).status_code, 404)
        self.assertEqual(self.client.get(class_url).status_code, 404)

    def test_retired_create_is_inert_and_api_rejects_another_course_bank(self):
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

        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            f"/api/v1/teacher/courses/{self.course_a.pk}/experiments/",
            {"name": "越界实验", "question_bank_id": self.bank_b.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('question_bank_id', domain_payload(response)['error']['fields'])
        self.assertFalse(Experiment.objects.filter(name="越界实验").exists())

    def test_experiment_assignment_rejects_student_from_another_course(self):
        experiment = Experiment.objects.create(
            course=self.course_a,
            created_by=self.teacher,
            question_bank=self.bank_a,
            name="A 实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        started = self.client.post(f"/api/v1/teacher/courses/{self.course_a.pk}/experiments/{experiment.pk}/publish-plans/")
        self.assertEqual(started.status_code, 201, started.content)
        plan = domain_payload(started)
        for class_id, exclusions in ((self.class_b.pk, []), (self.class_a.pk, [self.student_b.pk])):
            with self.subTest(class_id=class_id, exclusions=exclusions):
                response = self.client.put(f"/api/v1/teacher/publish-plans/{plan['id']}/setup/", {
                    "expected_revision": plan['plan_revision'],
                    "classes": [{"class_id": class_id, "excluded_student_ids": exclusions}],
                    "default_schedule": {"opens_at": None, "closes_at": None}, "schedules": [],
                }, content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertTrue(domain_payload(response)['error']['fields'])
        self.assertFalse(ExperimentStudent.objects.filter(experiment=experiment).exists())

    def test_retired_edit_is_inert_and_api_rejects_another_course_bank(self):
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

        self.assertEqual(response.status_code, 404)
        response = self.client.patch(
            f"/api/v1/teacher/courses/{self.course_a.pk}/experiments/{experiment.pk}/",
            {"question_bank_id": self.bank_b.pk}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('question_bank_id', domain_payload(response)['error']['fields'])
        experiment.refresh_from_db()
        self.assertEqual(experiment.question_bank, self.bank_a)
