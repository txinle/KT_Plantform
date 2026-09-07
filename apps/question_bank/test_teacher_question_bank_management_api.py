import json
import os
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Course, User
from apps.question_bank.models import Concept, Question, QuestionBank


class TeacherQuestionBankManagementApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="bank-management-owner",
            display_name="题库负责人",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="当前课程",
            code="CURRENT-BANK",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="当前题库",
            description="原始说明",
        )
        self.root = Concept.objects.create(
            course=self.course,
            code="KC1",
            name="基础数学",
        )
        self.child = Concept.objects.create(
            course=self.course,
            code="KC2",
            name="基础运算",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="原题目",
            correct_answer="原答案",
            status=Question.Status.DRAFT,
        )
        self.client.force_login(self.teacher)

    def course_url(self, suffix):
        return f"/api/v1/teacher/courses/{self.course.pk}/{suffix}"

    def test_archived_bank_collection_restore_and_delete_are_json_actions(self):
        archived = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="已归档题库",
            visibility=QuestionBank.Visibility.ARCHIVED,
        )

        listed = self.client.get(self.course_url("banks/archived/"))

        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()["items"][0]["id"], archived.pk)
        restored = self.client.post(
            self.course_url(f"banks/{archived.pk}/restore/"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(restored.status_code, 200, restored.content)
        archived.refresh_from_db()
        self.assertEqual(archived.visibility, QuestionBank.Visibility.PRIVATE)

        deleted = self.client.delete(
            self.course_url(f"banks/{archived.pk}/delete/"),
            data=json.dumps({"confirmation": "delete"}),
            content_type="application/json",
        )
        self.assertEqual(deleted.status_code, 204, deleted.content)
        self.assertFalse(QuestionBank.objects.filter(pk=archived.pk).exists())

    def test_shared_source_creator_bank_selection_and_copy_preserve_private_target(self):
        source_owner = User.objects.create_user(
            username="bank-management-source-owner",
            display_name="来源教师",
            role=User.Role.TEACHER,
        )
        source_course = Course.objects.create(
            owner=source_owner,
            name="来源课程",
            code="SOURCE-BANK",
            term="2026 秋",
        )
        source = QuestionBank.objects.create(
            course=source_course,
            created_by=source_owner,
            name="共享源题库",
            visibility=QuestionBank.Visibility.SHARED,
        )
        Question.objects.create(
            bank=source,
            sequence=1,
            question_type=Question.Type.TRUE_FALSE,
            text="来源题目",
            correct_answer=True,
            status=Question.Status.DRAFT,
        )

        sources = self.client.get(self.course_url("banks/shared/"))
        self.assertEqual(sources.status_code, 200, sources.content)
        self.assertEqual(sources.json()["items"][0]["id"], source_course.pk)

        creators = self.client.get(
            self.course_url(f"banks/shared/{source_course.pk}/")
        )
        self.assertEqual(creators.status_code, 200, creators.content)
        self.assertEqual(creators.json()["items"][0]["id"], source_owner.pk)

        banks = self.client.get(
            self.course_url(
                f"banks/shared/{source_course.pk}/creators/{source_owner.pk}/"
            )
        )
        self.assertEqual(banks.status_code, 200, banks.content)
        self.assertEqual(banks.json()["items"][0]["id"], source.pk)

        copied = self.client.post(
            self.course_url(
                f"banks/shared/{source_course.pk}/creators/{source_owner.pk}/"
                f"banks/{source.pk}/copy/"
            ),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(copied.status_code, 201, copied.content)
        copy = QuestionBank.objects.get(pk=copied.json()["id"])
        self.assertEqual(copy.course_id, self.course.pk)
        self.assertEqual(copy.visibility, QuestionBank.Visibility.PRIVATE)
        self.assertEqual(copy.questions.count(), 1)

    def test_bank_editor_uses_metadata_form_and_keeps_archived_visibility_constrained(self):
        editor_url = self.course_url(f"banks/{self.bank.pk}/editor/")
        initial = self.client.get(editor_url)
        self.assertEqual(initial.status_code, 200, initial.content)
        self.assertEqual(initial.json()["name"], self.bank.name)

        updated = self.client.put(
            editor_url,
            data=json.dumps(
                {
                    "name": "  更新后的题库  ",
                    "description": "更新后的说明",
                    "visibility": "shared",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.name, "更新后的题库")
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)

        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=["visibility", "updated_at"])
        invalid = self.client.put(
            editor_url,
            data=json.dumps(
                {
                    "name": self.bank.name,
                    "description": self.bank.description,
                    "visibility": "shared",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertIn("visibility", invalid.json()["error"]["fields"])
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.ARCHIVED)

    def test_question_editor_reuses_question_form_and_persists_the_validated_kc_tree(self):
        editor_url = self.course_url(
            f"banks/{self.bank.pk}/questions/{self.question.pk}/editor/"
        )
        initial = self.client.get(editor_url)
        self.assertEqual(initial.status_code, 200, initial.content)
        self.assertEqual(initial.json()["question"]["correct_answer_text"], "原答案")
        self.assertEqual(
            [item["kc_code"] for item in initial.json()["concepts"]],
            ["KC1", "KC2"],
        )

        mapping = {
            "schema_version": "question-kc-tree-v2",
            "root_kc_code": "KC1",
            "nodes": [
                {"kc_code": "KC1", "parent_kc_code": None, "order": 1},
                {"kc_code": "KC2", "parent_kc_code": "KC1", "order": 1},
            ],
        }
        updated = self.client.put(
            editor_url,
            data=json.dumps(
                {
                    "text": "更新题目",
                    "question_type": Question.Type.SHORT,
                    "status": Question.Status.ACTIVE,
                    "correct_answer_text": "更新答案",
                    "accepted_answers_text": "近似答案；替代答案",
                    "teacher_difficulty": "0.500",
                    "absolute_tolerance": "",
                    "relative_tolerance": "",
                    "explanation": "更新解析",
                    "option_a": "",
                    "option_b": "",
                    "option_c": "",
                    "option_d": "",
                    "option_e": "",
                    "option_f": "",
                    "kc_mapping_json": json.dumps(mapping),
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.text, "更新题目")
        self.assertEqual(self.question.status, Question.Status.ACTIVE)
        self.assertEqual(self.question.accepted_answers, ["近似答案", "替代答案"])
        self.assertEqual(
            list(self.question.concept_links.order_by("display_order").values_list("concept__code", flat=True)),
            ["KC1", "KC2"],
        )

    def test_question_import_preview_and_confirmation_do_not_write_until_confirmed(self):
        from .test_unified_question_import import question_row, upload_rows
        preview_url = self.course_url(
            f"banks/{self.bank.pk}/imports/questions/preview/"
        )
        missing_file = self.client.post(
            preview_url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(missing_file.status_code, 400, missing_file.content)
        self.assertEqual(missing_file.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("file", missing_file.json()["error"]["fields"])

        preview = self.client.post(
            preview_url,
            {
                "file": upload_rows([question_row(题目='导入题目', 题型='判断题', 答案='正确', 二级知识点='', 三级知识点='')])
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertEqual(self.bank.questions.count(), 1)
        self.assertEqual(preview.json()["preview"]["rows"][0]["classification"], 'ready')

        confirmed = self.client.post(
            self.course_url(f"banks/{self.bank.pk}/imports/questions/confirm/"),
            data=json.dumps({"preview_id": preview.json()["preview_id"]}),
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.content)
        self.assertTrue(self.bank.questions.filter(sequence=2, text="导入题目").exists())

    def test_question_kc_import_uses_the_persistent_preview_before_confirming(self):
        preview_url = self.course_url(
            f"banks/{self.bank.pk}/imports/kc/preview/"
        )
        preview = self.client.post(
            preview_url,
            {
                "file": SimpleUploadedFile(
                    "question-kcs.csv",
                    (
                        "题目序号,一级知识点,二级知识点,三级知识点\n"
                        "1,KC1,KC2,\n"
                    ).encode("utf-8-sig"),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertFalse(self.question.concept_links.exists())

        confirmed = self.client.post(
            self.course_url(f"banks/{self.bank.pk}/imports/kc/confirm/"),
            data=json.dumps({"batch_id": preview.json()["batch_id"]}),
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.primary_concept.pk, self.root.pk)


class TeacherStandaloneQuestionBankManagementApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="standalone-bank-owner",
            display_name="独立题库教师",
            role=User.Role.TEACHER,
        )
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="独立题库",
            description="不绑定课程",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="独立题目",
            correct_answer="原答案",
            status=Question.Status.DRAFT,
        )
        self.client.force_login(self.teacher)

    def test_collection_create_shared_copy_and_concept_list_are_json(self):
        source_owner = User.objects.create_user(
            username="standalone-shared-owner",
            role=User.Role.TEACHER,
        )
        shared = QuestionBank.objects.create(
            owner=source_owner,
            name="可复制的共享题库",
            visibility=QuestionBank.Visibility.SHARED,
        )

        listed = self.client.get("/api/v1/teacher/banks/")

        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertEqual(listed.json()["banks"][0]["id"], self.bank.pk)
        self.assertEqual(listed.json()["shared_banks"][0]["id"], shared.pk)

        created = self.client.post(
            "/api/v1/teacher/banks/",
            data=json.dumps(
                {"name": "新独立题库", "description": "", "visibility": "private"}
            ),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        self.assertIsNone(QuestionBank.objects.get(pk=created.json()["id"]).course_id)

        copied = self.client.post(
            f"/api/v1/teacher/banks/shared/{shared.pk}/copy/",
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(copied.status_code, 201, copied.content)
        copy = QuestionBank.objects.get(pk=copied.json()["id"])
        self.assertEqual(copy.owner_id, self.teacher.pk)
        self.assertIsNone(copy.course_id)

        concepts = self.client.get("/api/v1/teacher/concepts/")
        self.assertEqual(concepts.status_code, 200, concepts.content)
        self.assertEqual(concepts.json()["items"], [])

        course = Course.objects.create(
            owner=self.teacher,
            name="规范课程",
            code="CANONICAL-BANK",
            term="2026 秋",
        )
        course_bank = QuestionBank.objects.create(
            course=course,
            created_by=self.teacher,
            name="课程题库",
        )
        course_question = Question.objects.create(
            bank=course_bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="课程题目",
            correct_answer="答案",
            status=Question.Status.DRAFT,
        )
        canonical_list = self.client.get("/api/v1/teacher/banks/")
        self.assertEqual(canonical_list.status_code, 200, canonical_list.content)
        self.assertEqual(
            canonical_list.json()["redirect_url"],
            "/teacher/courses/",
        )
        canonical_concepts = self.client.get("/api/v1/teacher/concepts/")
        self.assertEqual(canonical_concepts.status_code, 200, canonical_concepts.content)
        self.assertEqual(
            canonical_concepts.json()["redirect_url"],
            "/teacher/courses/",
        )
        canonical = self.client.get(f"/api/v1/teacher/banks/{course_bank.pk}/")
        self.assertEqual(canonical.status_code, 200, canonical.content)
        self.assertEqual(
            canonical.json()["redirect_url"],
            f"/teacher/courses/{course.pk}/banks/{course_bank.pk}/",
        )
        question_import = self.client.get(
            f"/api/v1/teacher/banks/{course_bank.pk}/?destination=question_import"
        )
        self.assertEqual(question_import.status_code, 200, question_import.content)
        self.assertEqual(
            question_import.json()["redirect_url"],
            f"/teacher/courses/{course.pk}/banks/{course_bank.pk}/import/",
        )
        kc_import = self.client.get(
            f"/api/v1/teacher/banks/{course_bank.pk}/?destination=kc_import"
        )
        self.assertEqual(kc_import.status_code, 200, kc_import.content)
        self.assertEqual(
            kc_import.json()["redirect_url"],
            f"/teacher/courses/{course.pk}/banks/{course_bank.pk}/kc-import/",
        )
        question_editor = self.client.get(
            f"/api/v1/teacher/banks/{course_bank.pk}/?destination=question_editor&question_id={course_question.pk}"
        )
        self.assertEqual(question_editor.status_code, 200, question_editor.content)
        self.assertEqual(
            question_editor.json()["redirect_url"],
            f"/teacher/courses/{course.pk}/banks/{course_bank.pk}/questions/{course_question.pk}/edit/",
        )
        wrong_question = self.client.get(
            f"/api/v1/teacher/banks/{course_bank.pk}/?destination=question_editor&question_id={self.question.pk}"
        )
        self.assertEqual(wrong_question.status_code, 404, wrong_question.content)

    def test_student_cannot_access_the_teacher_standalone_api(self):
        course = Course.objects.create(
            owner=self.teacher,
            name="教师课程",
            code="TEACHER-ONLY-BANK",
            term="2026 秋",
        )
        student = User.objects.create_user(
            username="standalone-bank-student",
            role=User.Role.STUDENT,
        )
        self.client.force_login(student)

        collection = self.client.get("/api/v1/teacher/banks/")
        self.assertEqual(collection.status_code, 403, collection.content)
        create = self.client.post(
            "/api/v1/teacher/banks/",
            data=json.dumps(
                {"name": "学生不能创建", "description": "", "visibility": "private"}
            ),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 403, create.content)
        concepts = self.client.get("/api/v1/teacher/concepts/")
        self.assertEqual(concepts.status_code, 403, concepts.content)
        course_banks = self.client.get(
            f"/api/v1/teacher/courses/{course.pk}/banks/archived/"
        )
        self.assertEqual(course_banks.status_code, 403, course_banks.content)

    def test_question_edit_and_question_import_keep_two_phase_semantics_without_course(self):
        editor_url = f"/api/v1/teacher/banks/{self.bank.pk}/questions/{self.question.pk}/editor/"
        editor = self.client.get(editor_url)
        self.assertEqual(editor.status_code, 200, editor.content)
        self.assertEqual(editor.json()["concepts"], [])

        updated = self.client.put(
            editor_url,
            data=json.dumps(
                {
                    "text": "更新后的独立题目",
                    "question_type": Question.Type.SHORT,
                    "status": Question.Status.DRAFT,
                    "correct_answer_text": "更新答案",
                    "accepted_answers_text": "备选答案",
                    "teacher_difficulty": "",
                    "absolute_tolerance": "",
                    "relative_tolerance": "",
                    "explanation": "",
                    "option_a": "",
                    "option_b": "",
                    "option_c": "",
                    "option_d": "",
                    "option_e": "",
                    "option_f": "",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.question.refresh_from_db()
        self.assertEqual(self.question.text, "更新后的独立题目")

        preview = self.client.post(
            f"/api/v1/teacher/banks/{self.bank.pk}/imports/questions/preview/",
            {
                "file": SimpleUploadedFile(
                    "questions.csv",
                    "序号,题目,题型,答案\n2,新独立题目,判断题,正确\n".encode("utf-8-sig"),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        self.assertFalse(self.bank.questions.filter(sequence=2).exists())

        confirmed = self.client.post(
            f"/api/v1/teacher/banks/{self.bank.pk}/imports/questions/confirm/",
            data=json.dumps({"preview_id": preview.json()["preview_id"]}),
            content_type="application/json",
        )
        self.assertEqual(confirmed.status_code, 201, confirmed.content)
        self.assertTrue(self.bank.questions.filter(sequence=2).exists())

        kc_preview = self.client.post(
            f"/api/v1/teacher/banks/{self.bank.pk}/imports/kc/preview/",
            {
                "file": SimpleUploadedFile(
                    "question-kcs.csv",
                    "序号,主知识点,次知识点\n1,基础知识,\n".encode(
                        "utf-8-sig"
                    ),
                    content_type="text/csv",
                )
            },
        )
        self.assertEqual(kc_preview.status_code, 200, kc_preview.content)
        kc_confirmed = self.client.post(
            f"/api/v1/teacher/banks/{self.bank.pk}/imports/kc/confirm/",
            data=json.dumps({"preview_id": kc_preview.json()["preview_id"]}),
            content_type="application/json",
        )
        self.assertEqual(kc_confirmed.status_code, 409, kc_confirmed.content)
        self.assertEqual(
            kc_confirmed.json()["error"]["code"], "standalone_kc_mapping_unsupported"
        )

    @override_settings(DEBUG=True, VITE_DEV_SERVER_URL="http://localhost:5173")
    def test_user_allowlist_serves_vue_shell_for_no_course_urls(self):
        urls = [
            "/teacher/banks/",
            f"/teacher/banks/{self.bank.pk}/",
            f"/teacher/banks/{self.bank.pk}/import/",
            f"/teacher/banks/{self.bank.pk}/kc-import/",
            f"/teacher/banks/{self.bank.pk}/questions/{self.question.pk}/edit/",
            "/teacher/concepts/",
        ]
        with patch.dict(
            os.environ,
            {
                "VUE_TEACHER_QUESTION_BANKS_ENABLED": "1",
                "VUE_TEACHER_QUESTION_BANKS_COURSE_IDS": "",
                "VUE_TEACHER_QUESTION_BANKS_USER_IDS": str(self.teacher.pk),
            },
        ):
            responses = [self.client.get(url) for url in urls]

        for response in responses:
            self.assertEqual(response.status_code, 200, response.content)
            self.assertContains(response, 'id="kt-app"')
            self.assertContains(response, 'data-module="teacher-question-banks"')
