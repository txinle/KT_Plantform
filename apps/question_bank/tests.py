from django.test import TestCase
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
