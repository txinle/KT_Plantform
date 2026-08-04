from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from .models import Question, QuestionBank
from .services.import_parsing import parse_kc_rows, parse_question_rows
from .services.import_service import import_kc_mappings, import_questions

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
        self.assertContains(self.response, "data-visibility-control")
        self.assertContains(self.response, "data-auto-submit")
        self.assertContains(self.response, f'action="{self.visibility_url}"')
        for value, label in QuestionBank.Visibility.choices:
            self.assertContains(
                self.response,
                f'<option value="{value}"',
            )
