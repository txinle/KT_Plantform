import os
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Course, User
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
)


class TeacherQuestionBankApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="teacher-bank-api-owner",
            display_name="题库教师",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="题库 API 课程",
            code="BANK-API",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.owner,
            name="核心题库",
            description="覆盖知识点与题目分页",
        )
        self.math = Concept.objects.create(course=self.course, name="基础运算")
        self.prime = Concept.objects.create(course=self.course, name="质数")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="2 + 2 等于多少？",
            correct_answer="B",
            explanation="不得进入题目清单 DTO",
            teacher_difficulty="0.125",
        )
        QuestionConcept.objects.create(
            question=self.question,
            concept=self.math,
            is_primary=True,
        )
        QuestionConcept.objects.create(
            question=self.question,
            concept=self.prime,
            is_primary=False,
        )
        self.client.force_login(self.owner)

    def test_bank_collection_is_filtered_and_stably_paginated_at_twenty(self):
        for index in range(20):
            QuestionBank.objects.create(
                course=self.course,
                created_by=self.owner,
                name=f"分页题库 {index:02d}",
            )
        QuestionBank.objects.create(
            course=self.course,
            created_by=self.owner,
            name="归档题库",
            visibility=QuestionBank.Visibility.ARCHIVED,
        )

        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 20)
        self.assertEqual(
            payload["page"],
            {
                "number": 1,
                "size": 20,
                "total_items": 21,
                "total_pages": 2,
                "start": 1,
                "end": 20,
            },
        )
        self.assertEqual(payload["course"]["id"], self.course.pk)
        self.assertEqual(payload["summary"]["archived_count"], 1)
        self.assertEqual(
            payload["summary"]["archived_banks_url"],
            reverse("courses:archived_banks", args=[self.course.pk]),
        )

        searched = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/",
            {"q": "核心"},
        ).json()
        self.assertEqual(searched["page"]["total_items"], 1)
        item = searched["items"][0]
        self.assertEqual(item["name"], "核心题库")
        self.assertEqual(item["question_count"], 1)
        self.assertEqual(
            item["detail_url"],
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk]),
        )
        self.assertNotIn("owner_id", item)

    def test_course_concepts_are_scoped_paginated_and_include_bank_distribution(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/",
            {"q": "基础"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"]["size"], 20)
        self.assertEqual(payload["page"]["total_items"], 1)
        concept = payload["items"][0]
        self.assertEqual(concept["name"], "基础运算")
        self.assertEqual(concept["question_count"], 1)
        self.assertEqual(concept["bank_count"], 1)
        self.assertEqual(concept["distribution"][0]["bank_name"], "核心题库")
        self.assertFalse(concept["can_delete"])

    def test_bank_detail_and_question_collection_expose_only_list_safe_fields(self):
        detail = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/"
        )
        questions = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/"
            f"{self.bank.pk}/questions/",
            {"q": "2 + 2", "question_type": Question.Type.SINGLE},
        )

        self.assertEqual(detail.status_code, 200)
        bank = detail.json()
        self.assertEqual(bank["id"], self.bank.pk)
        self.assertTrue(bank["can_edit_metadata"])
        self.assertTrue(bank["can_edit_content"])
        self.assertEqual(
            bank["question_import_url"],
            reverse("courses:question_import", args=[self.course.pk, self.bank.pk]),
        )

        self.assertEqual(questions.status_code, 200)
        payload = questions.json()
        self.assertEqual(payload["page"]["total_items"], 1)
        item = payload["items"][0]
        self.assertEqual(item["course_sequence"], 1)
        self.assertTrue(item["stable_code"].startswith("C"))
        self.assertEqual(item["question_type_label"], "单选题")
        self.assertEqual(item["primary_concept"]["name"], "基础运算")
        self.assertEqual(item["teacher_difficulty"], "0.125")
        self.assertEqual(
            item["edit_url"],
            reverse(
                "courses:question_edit",
                args=[self.course.pk, self.bank.pk, self.question.pk],
            ),
        )
        for forbidden in (
            "correct_answer",
            "accepted_answers",
            "explanation",
            "options",
        ):
            self.assertNotIn(forbidden, item)

    def test_bank_concepts_are_paginated_from_the_full_bank_mapping(self):
        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/"
            f"{self.bank.pk}/concepts/"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["page"]["total_items"], 2)
        rows = {row["name"]: row for row in payload["items"]}
        self.assertEqual(rows["基础运算"]["primary_count"], 1)
        self.assertEqual(rows["基础运算"]["secondary_count"], 0)
        self.assertEqual(rows["质数"]["primary_count"], 0)
        self.assertEqual(rows["质数"]["secondary_count"], 1)
        self.assertEqual(rows["基础运算"]["questions"][0]["code"], "Q0001")

    def test_unknown_cross_course_and_inaccessible_scopes_are_hidden_as_404(self):
        other_course = Course.objects.create(
            owner=self.owner,
            name="其他课程",
            code="BANK-OTHER",
            term="2026 秋",
        )
        outsider = User.objects.create_user(
            username="teacher-bank-api-outsider",
            role=User.Role.TEACHER,
        )

        cross_course = self.client.get(
            f"/api/v1/teacher/courses/{other_course.pk}/banks/{self.bank.pk}/"
        )
        self.client.force_login(outsider)
        inaccessible = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/"
        )

        self.assertEqual(cross_course.status_code, 404)
        self.assertEqual(inaccessible.status_code, 404)


@override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
class TeacherQuestionBankVueShellTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="teacher-bank-shell-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="题库 Shell 课程",
            code="BANK-SHELL",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="试点题库",
        )
        self.list_url = reverse("courses:banks", args=[self.course.pk])
        self.detail_url = reverse(
            "courses:bank_detail", args=[self.course.pk, self.bank.pk]
        )
        self.client.force_login(self.teacher)

    def rollout_env(self, **values):
        defaults = {
            "VUE_TEACHER_QUESTION_BANKS_ENABLED": "0",
            "VUE_TEACHER_QUESTION_BANKS_COURSE_IDS": "",
            "VUE_TEACHER_QUESTION_BANKS_USER_IDS": "",
        }
        defaults.update(values)
        return patch.dict(os.environ, defaults)

    def test_flag_off_preserves_question_bank_templates(self):
        with self.rollout_env():
            bank_list = self.client.get(self.list_url)
            bank_detail = self.client.get(self.detail_url)

        self.assertTemplateUsed(bank_list, "teacher/course_bank_list.html")
        self.assertTemplateUsed(bank_detail, "teacher/course_bank_detail.html")
        self.assertNotContains(bank_list, 'id="kt-app"')
        self.assertNotContains(bank_detail, 'id="kt-app"')

    def test_pilot_course_receives_vue_shell_on_existing_read_routes(self):
        with self.rollout_env(
            VUE_TEACHER_QUESTION_BANKS_ENABLED="1",
            VUE_TEACHER_QUESTION_BANKS_COURSE_IDS=str(self.course.pk),
        ):
            bank_list = self.client.get(self.list_url)
            bank_detail = self.client.get(self.detail_url)

        for response in (bank_list, bank_detail):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="kt-app"')
            self.assertContains(response, 'data-module="teacher-question-banks"')

    def test_nonpilot_and_invalid_create_keep_the_legacy_document_flow(self):
        other_course = Course.objects.create(
            owner=self.teacher,
            name="非试点课程",
            code="BANK-SHELL-OTHER",
            term="2026 秋",
        )
        with self.rollout_env(
            VUE_TEACHER_QUESTION_BANKS_ENABLED="1",
            VUE_TEACHER_QUESTION_BANKS_COURSE_IDS=str(self.course.pk),
        ):
            nonpilot = self.client.get(
                reverse("courses:banks", args=[other_course.pk])
            )
            invalid_create = self.client.post(
                self.list_url,
                {"name": "", "description": "", "visibility": "private"},
            )

        self.assertTemplateUsed(nonpilot, "teacher/course_bank_list.html")
        self.assertTemplateUsed(invalid_create, "teacher/course_bank_list.html")
        self.assertContains(invalid_create, "该字段是必填项")
        self.assertNotContains(nonpilot, 'id="kt-app"')
        self.assertNotContains(invalid_create, 'id="kt-app"')
