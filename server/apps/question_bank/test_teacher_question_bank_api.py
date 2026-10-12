from apps.api.test_support import domain_payload
import os
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Course, User
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
)
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping


class CourseConceptCreateTransactionTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="kc-create-owner", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="新增 KC 事务", code="KC-TX", term="2026")
        self.url = f"/api/v1/teacher/courses/{self.course.pk}/concepts/"
        self.client.force_login(self.teacher)

    def test_create_under_autocommit_and_reject_duplicate_without_partial_write(self):
        payload = {"sequence": 1, "name": "基础运算", "description": "说明"}
        response = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        concept = self.course.concepts.get()
        self.assertEqual((concept.code, concept.name, concept.description), ("KC1", "基础运算", "说明"))
        repeated = self.client.post(self.url, data=payload, content_type="application/json")
        self.assertEqual(repeated.status_code, 400, repeated.content)
        self.assertEqual(self.course.concepts.count(), 1)

    def test_archived_course_rejects_creation_without_partial_write(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        response = self.client.post(self.url, data={"sequence": 1, "name": "基础运算"}, content_type="application/json")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(self.course.concepts.exists())


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
        self.root = Concept.objects.create(course=self.course, code="KC1", name="数学")
        self.number = Concept.objects.create(course=self.course, code="KC2", name="数与运算")
        self.math = Concept.objects.create(course=self.course, code="KC3", name="基础运算")
        self.prime = Concept.objects.create(course=self.course, code="KC4", name="质数")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="2 + 2 等于多少？",
            correct_answer="B",
            explanation="不得进入题目清单 DTO",
            teacher_difficulty="0.125",
        )
        replace_question_kc_mapping(
            self.question,
            root_kc_code=self.root.code,
            nodes=[
                {"kc_code": self.root.code, "parent_kc_code": None, "order": 1},
                {"kc_code": self.number.code, "parent_kc_code": self.root.code, "order": 1},
                {"kc_code": self.math.code, "parent_kc_code": self.number.code, "order": 1},
                {"kc_code": self.prime.code, "parent_kc_code": self.number.code, "order": 2},
            ],
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
        payload = domain_payload(response)
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

        searched = domain_payload(self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/",
            {"q": "核心"},
        ))
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
        payload = domain_payload(response)
        self.assertEqual(payload["page"]["size"], 20)
        self.assertEqual(payload["page"]["total_items"], 1)
        concept = payload["items"][0]
        self.assertEqual(concept["name"], "基础运算")
        self.assertEqual(concept["kc_code"], self.math.code)
        self.assertEqual(concept["role_counts"], {"level_1": 0, "level_2": 0, "level_3": 1})
        self.assertEqual(concept["question_count"], 1)
        self.assertEqual(concept["bank_count"], 1)
        self.assertEqual(concept["distribution"][0]["bank_name"], "核心题库")
        self.assertTrue(concept["can_delete"])

        tree = domain_payload(self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/",
            {"all": "1"},
        ))
        self.assertEqual(tree["page"]["total_items"], 4)
        self.assertEqual(len(tree["items"]), 4)
        self.assertEqual(tree["summary"], {"total": 4, "active": 4, "used": 4, "archived": 0})

    def test_course_concepts_use_numeric_kc_code_order(self):
        Concept.objects.create(course=self.course, code="KC10", name="第十节点")

        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/",
            {"all": "1"},
        )

        self.assertEqual(
            [item["kc_code"] for item in domain_payload(response)["items"]],
            ["KC1", "KC2", "KC3", "KC4", "KC10"],
        )

    def test_course_concept_full_tree_is_not_truncated_at_500_nodes(self):
        Concept.objects.bulk_create(
            [
                Concept(
                    course=self.course,
                    code=f"KC{index + 5}",
                    name=f"批量节点 {index:03d}",
                    normalized_name=f"批量节点 {index:03d}",
                )
                for index in range(501)
            ]
        )

        response = self.client.get(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/",
            {"all": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["page"]["total_items"], 505)
        self.assertEqual(len(domain_payload(response)["items"]), 505)

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
        bank = domain_payload(detail)
        self.assertEqual(bank["id"], self.bank.pk)
        self.assertTrue(bank["can_edit_metadata"])
        self.assertTrue(bank["can_edit_content"])
        self.assertEqual(
            bank["question_import_url"],
            reverse("courses:question_import", args=[self.course.pk, self.bank.pk]),
        )

        self.assertEqual(questions.status_code, 200)
        payload = domain_payload(questions)
        self.assertEqual(payload["page"]["total_items"], 1)
        item = payload["items"][0]
        self.assertEqual(item["course_sequence"], 1)
        self.assertTrue(item["stable_code"].startswith("C"))
        self.assertEqual(item["question_type_label"], "单选题")
        self.assertEqual(item["kc_mapping"]["root_kc_code"], self.root.code)
        self.assertEqual(len(item["kc_mapping"]["nodes"]), 4)
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
        payload = domain_payload(response)
        self.assertEqual(payload["page"]["total_items"], 4)
        rows = {row["name"]: row for row in payload["items"]}
        self.assertEqual(rows["数学"]["level_1_count"], 1)
        self.assertEqual(rows["数与运算"]["level_2_count"], 1)
        self.assertEqual(rows["基础运算"]["level_3_count"], 1)
        self.assertEqual(rows["质数"]["level_3_count"], 1)
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
