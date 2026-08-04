import re
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse
from apps.accounts.models import User
from .models import Question, QuestionBank
from .services.import_parsing import parse_kc_rows, parse_question_rows
from .services.import_service import import_kc_mappings, import_questions


class ImportTemplateDownloadTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="template-teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(username="template-student", role=User.Role.STUDENT)
        self.question_template_url = reverse("question_bank:question_import_template")
        self.kc_template_url = reverse("question_bank:kc_import_template")

    def test_teacher_can_download_question_import_template(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.question_template_url)
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
            self.assertIn('filename="question_import_template.csv"', response["Content-Disposition"])
            content = b"".join(response.streaming_content).decode("utf-8-sig")
            self.assertIn("序号,题目,选项A,选项B,选项C,选项D,选项E,选项F,答案", content)
            self.assertIn("Knowledge Tracing 的中文名称是什么？", content)
        finally:
            response.close()

    def test_teacher_can_download_kc_mapping_template(self):
        self.client.force_login(self.teacher)

        response = self.client.get(self.kc_template_url)
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
            self.assertIn('filename="kc_mapping_template.csv"', response["Content-Disposition"])
            content = b"".join(response.streaming_content).decode("utf-8-sig")
            self.assertIn("题目序号,主知识点,次知识点", content)
            self.assertIn("基础运算,整数加法", content)
        finally:
            response.close()

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.question_template_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('login')}?next={self.question_template_url}")

    def test_student_cannot_download_import_templates(self):
        self.client.force_login(self.student)

        response = self.client.get(self.question_template_url)

        self.assertEqual(response.status_code, 403)

    def test_missing_template_returns_not_found_without_path_disclosure(self):
        self.client.force_login(self.teacher)
        with TemporaryDirectory() as directory:
            with override_settings(BASE_DIR=Path(directory)):
                response = self.client.get(self.question_template_url)

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(directory, response.content.decode())


class ImportTemplateLinkTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="template-link-teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="Template links bank")
        self.client.force_login(self.teacher)

    def test_question_import_page_links_to_question_template(self):
        response = self.client.get(reverse("question_bank:question_import", args=[self.bank.pk]))

        self.assertContains(response, "下载题目 CSV 示例")
        self.assertContains(response, reverse("question_bank:question_import_template"))

    def test_kc_import_page_links_to_kc_template(self):
        response = self.client.get(reverse("question_bank:kc_import", args=[self.bank.pk]))

        self.assertContains(response, "下载 KC CSV 示例")
        self.assertContains(response, reverse("question_bank:kc_import_template"))

class QuestionImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="bank")

    def test_minimal_question_and_kc_import(self):
        questions = parse_question_rows([{"序号":1,"题目":"2+2=?","选项A":"3","选项B":"4","答案":"B"}])
        import_questions(self.bank, questions)
        mappings = parse_kc_rows([{"题目序号":1,"主知识点":"加法","次知识点":"整数"}])
        import_kc_mappings(self.bank, self.teacher, mappings)
        question = Question.objects.get(bank=self.bank)
        self.assertEqual(question.primary_concept.name, "加法")
        self.assertEqual(question.options.count(), 2)


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
