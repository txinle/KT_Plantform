import json

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Course, User

from .models import CourseQuestionIdentity, Question, QuestionBank


class CourseQuestionIdentityAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="identity-admin",
            password="test-password",
            role=User.Role.ADMIN,
        )
        self.teacher = User.objects.create_user(
            username="identity-admin-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="后台课程",
            code="ADMIN101",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="后台题库",
        )
        self.client.force_login(self.admin_user)

    def test_admin_add_automatically_assigns_a_course_identity(self):
        response = self.client.post(
            reverse("admin:question_bank_question_add"),
            {
                "bank": self.bank.pk,
                "sequence": 1,
                "code": "",
                "question_type": Question.Type.SHORT,
                "text": "后台新建题目",
                "correct_answer": json.dumps("答案", ensure_ascii=False),
                "accepted_answers": "[]",
                "absolute_tolerance": "",
                "relative_tolerance": "",
                "explanation": "",
                "teacher_difficulty": "",
                "empirical_difficulty": "",
                "status": Question.Status.ACTIVE,
                "options-TOTAL_FORMS": 0,
                "options-INITIAL_FORMS": 0,
                "options-MIN_NUM_FORMS": 0,
                "options-MAX_NUM_FORMS": 1000,
                "concept_links-TOTAL_FORMS": 0,
                "concept_links-INITIAL_FORMS": 0,
                "concept_links-MIN_NUM_FORMS": 0,
                "concept_links-MAX_NUM_FORMS": 1000,
                "_save": "保存",
            },
        )

        self.assertEqual(response.status_code, 302)
        question = Question.objects.get(bank=self.bank, sequence=1)
        self.assertTrue(
            CourseQuestionIdentity.objects.filter(
                question=question,
                course=self.course,
            ).exists()
        )
