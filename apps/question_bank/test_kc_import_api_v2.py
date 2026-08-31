from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.accounts.models import Course, User
from apps.audit.models import AuditLog

from .models import Concept, Question, QuestionBank


class KCImportApiV2Tests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="kc-import-api-v2", role=User.Role.TEACHER)
        self.course = Course.objects.create(
            owner=self.teacher,
            name="KC 导入 API 课程",
            code="KC-IMPORT-API",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="KC 导入 API 题库",
        )
        Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="导入映射题目",
            correct_answer="答案",
        )
        self.client.force_login(self.teacher)

    def test_vue_mutation_endpoints_create_bank_and_course_kc_through_authority(self):
        bank = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/",
            {"name": "新增题库", "description": "说明", "visibility": "private"},
            content_type="application/json",
        )
        concept = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/",
            {"sequence": 8, "name": "  数学，基础  ", "description": "字典节点"},
            content_type="application/json",
        )
        duplicate = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/",
            {"sequence": 9, "name": "数学基础"},
            content_type="application/json",
        )

        self.assertEqual(bank.status_code, 201, bank.content)
        self.assertEqual(concept.status_code, 201, concept.content)
        self.assertEqual(concept.json()["kc_code"], "KC8")
        self.assertEqual(duplicate.status_code, 400)

    def test_course_kc_preview_and_confirm_are_persisted_and_one_time(self):
        preview = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/concepts/import/preview/",
            {
                "file": SimpleUploadedFile(
                    "course-kcs.csv",
                    "序号,知识点名称\n1,数学基础\n2,数与运算\n".encode("utf-8-sig"),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        batch_id = preview.json()["batch_id"]
        self.assertEqual(preview.json()["preview"]["rows"][0]["classification"], "new")

        confirm_url = f"/api/v1/teacher/courses/{self.course.pk}/concepts/import/confirm/"
        confirmed = self.client.post(confirm_url, {"batch_id": batch_id}, content_type="application/json")
        repeated = self.client.post(confirm_url, {"batch_id": batch_id}, content_type="application/json")

        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(repeated.json()["code"], "preview_batch_consumed")
        self.assertEqual(list(self.course.concepts.order_by("code").values_list("code", flat=True)), ["KC1", "KC2"])
        audit = AuditLog.objects.get(action="course_kc.import")
        self.assertEqual(audit.details["batch_id"], batch_id)
        self.assertEqual(audit.details["created"], 2)

    def test_question_kc_preview_confirm_and_stale_conflict(self):
        root = Concept.objects.create(course=self.course, code="KC1", name="数学基础")
        Concept.objects.create(course=self.course, code="KC2", name="数与运算")
        preview = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/concepts/import/preview/",
            {
                "file": SimpleUploadedFile(
                    "mapping.csv",
                    "题目序号,一级知识点,二级知识点,三级知识点\n1,数学基础,KC2,\n".encode("utf-8-sig"),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        root.name = "数学基础（已改名）"
        root.save(update_fields=["name", "normalized_name"])

        confirmed = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/concepts/import/confirm/",
            {"batch_id": preview.json()["batch_id"]},
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 409)
        self.assertEqual(confirmed.json()["code"], "preview_stale")
        self.assertFalse(AuditLog.objects.filter(action="question_kc.import").exists())
