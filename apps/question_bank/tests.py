import csv
import re
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse
from apps.accounts.models import Course, User
from .models import Concept, Question, QuestionBank
from .services.import_parsing import parse_kc_rows, parse_question_kc_tree_rows, parse_question_rows
from .services.import_service import import_kc_mappings, import_questions


class ImportTemplateDownloadTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="template-teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="template-student", role=User.Role.STUDENT)
        self.question_template_url = reverse("question_bank:question_import_template")
        self.kc_template_url = reverse("question_bank:kc_import_template")
        self.course_kc_template_url = reverse("question_bank:course_kc_import_template")
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
            ],
        )
        parsed_questions = parse_question_rows(question_rows)
        self.assertEqual(len(parsed_questions), 6)
        self.assertEqual(
            [item.question_type for item in parsed_questions],
            ["single_choice", "multiple_choice", "numeric", "short_answer", "fill_blank", "true_false"],
        )
        self.assertEqual(parsed_questions[4].accepted_answers, ["知识跟踪", "KT"])
        self.assertIs(parsed_questions[5].answer, False)
        self.assertEqual(parsed_questions[0].explanation, "4 是唯一正确答案。")
        self.assertEqual(parsed_questions[0].teacher_difficulty, "0.100")
        self.assertIsNone(parsed_questions[3].teacher_difficulty)

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

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, f"{reverse('login')}?next={url}")

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


class ImportTemplateLinkTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="template-link-teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="Template links bank")
        self.client.force_login(self.teacher)

    def test_question_import_page_links_to_question_template(self):
        response = self.client.get(reverse("question_bank:question_import", args=[self.bank.pk]))
        template_url = reverse("question_bank:question_import_template")
        anchor = (
            f'<a class="btn btn-sm btn-outline-primary ms-2" href="{template_url}">'
            "下载题目 CSV 示例</a>"
        )

        self.assertInHTML(anchor, response.content.decode())
        self.assertEqual(response.content.decode().count(anchor), 1)
        self.assertContains(response, "中文列名与英文别名对照")
        self.assertContains(response, "六种题型")
        self.assertContains(response, "非选择题不要填写选项")
        self.assertNotContains(response, "下载 KC CSV 示例")

    def test_kc_import_page_links_to_kc_template(self):
        response = self.client.get(reverse("question_bank:kc_import", args=[self.bank.pk]))
        template_url = reverse("question_bank:kc_import_template")
        anchor = (
            f'<a class="btn btn-sm btn-outline-primary ms-2" href="{template_url}">'
            "下载四列 CSV 模板</a>"
        )

        self.assertInHTML(anchor, response.content.decode())
        self.assertEqual(response.content.decode().count(anchor), 1)
        self.assertContains(response, "唯一新标准为四列 KC 对应表")
        self.assertNotContains(response, "下载题目 CSV 示例")

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
        Question.objects.create(
            bank=self.bank,
            sequence=10,
            question_type=Question.Type.TRUE_FALSE,
            text="已有题目",
            correct_answer=True,
            status=Question.Status.DRAFT,
        )
        self.client.force_login(self.teacher)
        url = reverse("courses:question_import", args=[self.course.pk, self.bank.pk])
        upload = SimpleUploadedFile(
            "questions.csv",
            (
                "序号,题目,题型,答案,解析 ,难度\n"
                ",自动编号题,判断题,正确,自动编号解析,0.125\n"
                "20,显式编号题,判断题,错误,显式编号解析,\n"
            ).encode("utf-8-sig"),
            content_type="text/csv",
        )

        preview = self.client.post(url, {"file": upload})

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "21")
        self.assertContains(preview, "自动编号解析")
        self.assertContains(preview, "0.125")
        confirmed = self.client.post(url, {"action": "confirm"})
        self.assertRedirects(
            confirmed,
            reverse("courses:bank_detail", args=[self.course.pk, self.bank.pk]),
        )
        generated = self.bank.questions.get(sequence=21)
        self.assertEqual(generated.explanation, "自动编号解析")
        self.assertEqual(str(generated.teacher_difficulty), "0.125")
        self.assertIsNone(self.bank.questions.get(sequence=20).teacher_difficulty)

    def test_course_import_confirmation_is_atomic_when_previewed_sequence_becomes_occupied(self):
        self.client.force_login(self.teacher)
        url = reverse("courses:question_import", args=[self.course.pk, self.bank.pk])
        upload = SimpleUploadedFile(
            "questions.csv",
            "序号,题目,题型,答案\n,预览题,判断题,正确\n".encode("utf-8-sig"),
            content_type="text/csv",
        )
        preview = self.client.post(url, {"file": upload})
        self.assertEqual(preview.status_code, 200)
        previewed_sequence = self.client.session[
            f"course_question_import:{self.course.pk}:{self.bank.pk}"
        ][0]["sequence"]
        Question.objects.create(
            bank=self.bank,
            sequence=previewed_sequence,
            question_type=Question.Type.TRUE_FALSE,
            text="并发写入",
            correct_answer=True,
            status=Question.Status.DRAFT,
        )

        confirmed = self.client.post(url, {"action": "confirm"}, follow=True)

        self.assertEqual(confirmed.status_code, 200)
        self.assertContains(confirmed, "导入预览已失效")
        self.assertFalse(self.bank.questions.filter(text="预览题").exists())


class QuestionBankVisibilityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", role=User.Role.TEACHER)
        self.other_teacher = User.objects.create_user(username="other_teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.owner, name="private bank")
        self.url = reverse("question_bank:bank_visibility_update", args=[self.bank.pk])

    def test_owner_can_share_bank(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url, {"visibility": QuestionBank.Visibility.SHARED})

        self.assertRedirects(response, reverse("question_bank:bank_list"))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)

    def test_other_teacher_cannot_update_bank_visibility(self):
        self.client.force_login(self.other_teacher)

        response = self.client.post(self.url, {"visibility": QuestionBank.Visibility.SHARED})

        self.assertEqual(response.status_code, 404)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)

    def test_invalid_visibility_keeps_bank_private(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url, {"visibility": "public"}, follow=True)

        self.assertRedirects(response, reverse("question_bank:bank_list"))
        self.assertContains(response, "可见性更新失败，请重新选择")
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)

    def test_visibility_update_rejects_get(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)


class QuestionBankListUITests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="list-teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="Clickable bank")
        Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="Which option is correct?",
            correct_answer=["A"],
        )
        self.client.force_login(self.teacher)
        self.response = self.client.get(reverse("question_bank:bank_list"))
        self.detail_url = reverse("question_bank:bank_detail", args=[self.bank.pk])
        self.visibility_url = reverse("question_bank:bank_visibility_update", args=[self.bank.pk])

    def test_owned_bank_name_and_question_count_link_to_detail(self):
        rendered_bank = self.response.context["banks"].get(pk=self.bank.pk)
        self.assertTrue(hasattr(rendered_bank, "question_count"))
        if not hasattr(rendered_bank, "question_count"):
            return
        self.assertEqual(rendered_bank.question_count, 1)
        self.assertContains(
            self.response,
            f'<a class="cell-title" href="{self.detail_url}">Clickable bank</a>',
            html=True,
        )
        self.assertContains(
            self.response,
            (
                f'<a class="count-link" href="{self.detail_url}" '
                'aria-label="进入题库 Clickable bank，共 1 题">1</a>'
            ),
            html=True,
        )

    def test_owned_bank_visibility_control_includes_update_form_and_choices(self):
        content = self.response.content.decode()
        form_match = re.search(
            (
                rf'<form class="visibility-form" method="post" action="{re.escape(self.visibility_url)}">'
                r'(?P<body>.*?)</form>'
            ),
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(form_match)
        visibility_form = form_match.group(0)
        select_id = f"visibility-{self.bank.pk}"

        self.assertEqual(content.count(f'for="{select_id}"'), 1)
        self.assertEqual(content.count(f'id="{select_id}"'), 1)
        self.assertRegex(
            visibility_form,
            r'<input type="hidden" name="csrfmiddlewaretoken" value="[^"]+">',
        )
        self.assertInHTML(
            f'<label class="visually-hidden" for="{select_id}">题库可见性</label>',
            visibility_form,
        )
        select_match = re.search(
            (
                rf'<select class="form-select" id="{select_id}" name="visibility" data-auto-submit>'
                r'(?P<options>.*?)</select>'
            ),
            visibility_form,
            re.DOTALL,
        )
        self.assertIsNotNone(select_match)
        select_html = select_match.group(0)
        self.assertEqual(select_html.count("<option "), len(QuestionBank.Visibility.choices))
        for value, label in QuestionBank.Visibility.choices:
            selected = " selected" if value == QuestionBank.Visibility.PRIVATE else ""
            self.assertInHTML(
                f'<option value="{value}"{selected}>{label}</option>',
                select_html,
            )


class QuestionLabelDisplayTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="question-label-teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="Question labels")
        self.first_question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="First question",
            correct_answer="A",
        )
        self.second_question = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SINGLE,
            text="Second question",
            correct_answer="B",
        )
        self.client.force_login(self.teacher)

    def test_question_list_displays_unpadded_sequence_labels_without_changing_codes(self):
        response = self.client.get(reverse("question_bank:bank_detail", args=[self.bank.pk]))

        self.assertContains(response, '<div class="cell-meta mono">Q1</div>', html=True)
        self.assertContains(response, '<div class="cell-meta mono">Q2</div>', html=True)
        self.assertNotContains(response, '<div class="cell-meta mono">Q0001</div>', html=True)
        self.assertNotContains(response, '<div class="cell-meta mono">Q0002</div>', html=True)
        self.first_question.refresh_from_db()
        self.second_question.refresh_from_db()
        self.assertEqual(self.first_question.code, "Q0001")
        self.assertEqual(self.second_question.code, "Q0002")
