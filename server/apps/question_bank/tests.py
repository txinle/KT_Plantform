import csv
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse
from apps.accounts.models import Course, User
from .models import Concept, ImportPreviewBatch, Question, QuestionBank
from .services.import_parsing import parse_kc_rows, parse_question_kc_tree_rows, parse_question_rows
from .services.import_service import import_kc_mappings, import_questions


class ImportTemplateDownloadTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="template-teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="template-student", role=User.Role.STUDENT)
        self.question_template_url = reverse("teacher-question-import-template")
        self.kc_template_url = reverse("teacher-kc-import-template")
        self.course_kc_template_url = reverse("teacher-course-kc-import-template")
        self.templates = (
            (self.question_template_url, "question_import_template.csv"),
            (self.kc_template_url, "kc_mapping_template.csv"),
            (self.course_kc_template_url, "course_kc_template.csv"),
        )

    def test_teacher_can_download_complete_import_templates(self):
        self.client.force_login(self.teacher)

        for url, filename in self.templates:
            with self.subTest(filename=filename):
                response = self.client.get(url)
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
                    self.assertEqual(response["Content-Disposition"], f'attachment; filename="{filename}"')
                    self.assertEqual(
                        b"".join(response.streaming_content),
                        (settings.BASE_DIR / "fixtures" / filename).read_bytes(),
                    )
                finally:
                    response.close()

    def test_import_templates_use_utf8_bom_chinese_examples_accepted_by_parsers(self):
        question_bytes = (settings.BASE_DIR / "fixtures" / "question_import_template.csv").read_bytes()
        self.assertTrue(question_bytes.startswith(b"\xef\xbb\xbf"))
        question_text = question_bytes.decode("utf-8-sig")
        question_reader = csv.DictReader(StringIO(question_text))
        question_rows = list(question_reader)

        self.assertEqual(
            question_reader.fieldnames,
            [
                "序号",
                "题目",
                "题型",
                "选项A",
                "选项B",
                "选项C",
                "选项D",
                "选项E",
                "选项F",
                "答案",
                "可接受答案",
                "解析",
                "难度",
                "一级知识点",
                "二级知识点",
                "三级知识点",
            ],
        )
        parsed_questions = parse_question_rows(question_rows)
        self.assertEqual(len(parsed_questions), 6)
        self.assertEqual(
            [item.question_type for item in parsed_questions],
            ["single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer", "numeric"],
        )
        self.assertEqual(parsed_questions[3].accepted_answers, ["知识跟踪", "KT"])
        self.assertIs(parsed_questions[2].answer, False)
        self.assertEqual(parsed_questions[0].explanation, "4 是唯一正确答案。\n这是基础运算示例。")
        self.assertEqual(parsed_questions[0].teacher_difficulty, "0.100")
        self.assertIsNone(parsed_questions[4].teacher_difficulty)

        kc_bytes = (settings.BASE_DIR / "fixtures" / "kc_mapping_template.csv").read_bytes()
        self.assertTrue(kc_bytes.startswith(b"\xef\xbb\xbf"))
        kc_text = kc_bytes.decode("utf-8-sig")
        kc_reader = csv.DictReader(StringIO(kc_text))
        kc_rows = list(kc_reader)

        self.assertEqual(kc_reader.fieldnames, ["题目序号", "一级知识点", "二级知识点", "三级知识点"])
        parsed_mappings = parse_question_kc_tree_rows(kc_rows)
        self.assertEqual(len(parsed_mappings), 3)
        self.assertEqual(parsed_mappings[0].root, "KC1")
        self.assertEqual(parsed_mappings[0].level_two, ["KC2", "KC5"])
        self.assertEqual(parsed_mappings[0].level_three, [("KC2", "KC3"), ("KC2", "KC4"), ("KC5", "KC6")])

    def test_import_template_responses_close_opened_streams(self):
        self.client.force_login(self.teacher)

        for url, filename in self.templates:
            with self.subTest(filename=filename):
                expected_bytes = (settings.BASE_DIR / "fixtures" / filename).read_bytes()
                stream = BytesIO(expected_bytes)
                with patch.object(Path, "open", return_value=stream) as open_mock:
                    request = RequestFactory().get(url)
                    request.user = self.teacher
                    response = resolve(url).func(request)
                    try:
                        self.assertFalse(stream.closed)
                        self.assertEqual(b"".join(response.streaming_content), expected_bytes)
                    finally:
                        response.close()

                open_mock.assert_called_once_with("rb")
                self.assertTrue(stream.closed)

    def test_import_templates_reject_post(self):
        self.client.force_login(self.teacher)

        for url, filename in self.templates:
            with self.subTest(filename=filename):
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_anonymous_user_is_redirected_to_login(self):
        for url, filename in self.templates:
            with self.subTest(filename=filename):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 401)

    def test_student_cannot_download_import_templates(self):
        self.client.force_login(self.student)

        for url, filename in self.templates:
            with self.subTest(filename=filename):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 403)

    def test_missing_template_returns_not_found_without_path_disclosure(self):
        self.client.force_login(self.teacher)
        with TemporaryDirectory() as directory:
            with override_settings(BASE_DIR=Path(directory)):
                for url, filename in self.templates:
                    with self.subTest(filename=filename):
                        response = self.client.get(url)

                        self.assertEqual(response.status_code, 404)
                        self.assertNotIn(directory, response.content.decode())



class QuestionImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="course", code="IMPORT", term="2026 秋")
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name="bank")
        Concept.objects.create(course=self.course, code="KC1", name="加法")
        Concept.objects.create(course=self.course, code="KC2", name="整数")

    def test_minimal_question_and_kc_import(self):
        questions = parse_question_rows([{"序号":1,"题目":"2+2=?","选项A":"3","选项B":"4","答案":"B"}])
        import_questions(self.bank, questions)
        question = Question.objects.get(bank=self.bank)
        self.assertEqual(question.status, Question.Status.DRAFT)
        mappings = parse_kc_rows([{"题目序号":1,"主知识点":"加法","次知识点":"整数"}])
        import_kc_mappings(self.bank, self.teacher, mappings)
        question.refresh_from_db()
        self.assertEqual(question.status, Question.Status.ACTIVE)
        self.assertEqual(question.primary_concept.name, "加法")
        self.assertEqual(question.options.count(), 2)

    def test_course_import_previews_generated_sequences_and_persists_analysis_and_difficulty(self):
        from .test_unified_question_import import question_row, upload_rows
        Question.objects.create(
            bank=self.bank,
            sequence=10,
            question_type=Question.Type.TRUE_FALSE,
            text="已有题目",
            correct_answer=True,
            status=Question.Status.DRAFT,
        )
        self.client.force_login(self.teacher)
        url = reverse("teacher-question-import-preview", args=[self.course.pk, self.bank.pk])
        upload = upload_rows([
            question_row(序号='', 题目='自动编号题', 题型='判断题', 答案='正确', 解析='自动编号解析', 难度='0.125', 二级知识点='', 三级知识点=''),
            question_row(序号='20', 题目='显式编号题', 题型='判断题', 答案='错误', 解析='显式编号解析', 难度='', 二级知识点='', 三级知识点=''),
        ])

        preview = self.client.post(url, {"file": upload})

        self.assertEqual(preview.status_code, 200)
        preview_id = ImportPreviewBatch.objects.get().pk
        confirmed = self.client.post(url.replace('/preview/', '/confirm/'), {'action': 'confirm', 'preview_id': preview_id})
        self.assertEqual(confirmed.status_code, 201)
        generated = self.bank.questions.get(sequence=11)
        self.assertEqual(generated.explanation, "自动编号解析")
        self.assertEqual(str(generated.teacher_difficulty), "0.125")
        self.assertIsNone(self.bank.questions.get(sequence=12).teacher_difficulty)

    def test_course_import_confirmation_allocates_after_concurrent_append(self):
        from .test_unified_question_import import question_row, upload_rows
        self.client.force_login(self.teacher)
        url = reverse("teacher-question-import-preview", args=[self.course.pk, self.bank.pk])
        upload = upload_rows([question_row(题目='预览题', 二级知识点='', 三级知识点='')])
        preview = self.client.post(url, {"file": upload})
        self.assertEqual(preview.status_code, 200)
        Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.TRUE_FALSE,
            text="并发写入",
            correct_answer=True,
            status=Question.Status.DRAFT,
        )

        preview_id = ImportPreviewBatch.objects.get().pk
        confirmed = self.client.post(url.replace('/preview/', '/confirm/'), {'action': 'confirm', 'preview_id': preview_id})

        self.assertEqual(confirmed.status_code, 201)
        self.assertTrue(self.bank.questions.filter(text="预览题", sequence=2).exists())


class QuestionBankVisibilityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", role=User.Role.TEACHER)
        self.other_teacher = User.objects.create_user(username="other_teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.owner, name="private bank")
        self.url = reverse("teacher-standalone-question-bank-visibility", args=[self.bank.pk])

    def test_owner_can_share_bank(self):
        self.client.force_login(self.owner)

        response = self.client.put(self.url, {'visibility': QuestionBank.Visibility.SHARED}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)

    def test_other_teacher_cannot_update_bank_visibility(self):
        self.client.force_login(self.other_teacher)

        response = self.client.put(self.url, {'visibility': QuestionBank.Visibility.SHARED}, content_type='application/json')

        self.assertEqual(response.status_code, 404)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)

    def test_invalid_visibility_keeps_bank_private(self):
        self.client.force_login(self.owner)
        response = self.client.put(self.url, {'visibility': 'public'}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)

    def test_visibility_update_rejects_get(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
