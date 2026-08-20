from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User

from .forms import QuestionEditForm
from .models import Question, QuestionBank, QuestionOption
from .services.import_parsing import ParsedQuestion, parse_question_rows
from .services.import_service import import_questions


class QuestionTypeEditingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="question-type-teacher",
            role=User.Role.TEACHER,
        )
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="题型题库")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="原题干",
            correct_answer="原答案",
        )

    def payload(self, **overrides):
        data = {
            "text": "新题干",
            "question_type": Question.Type.SHORT,
            "teacher_difficulty": "",
            "absolute_tolerance": "",
            "relative_tolerance": "",
            "explanation": "",
            "status": Question.Status.ACTIVE,
            "accepted_answers_text": "",
            "option_a": "",
            "option_b": "",
            "option_c": "",
            "option_d": "",
            "option_e": "",
            "option_f": "",
            "correct_answer_text": "标准答案",
        }
        data.update(overrides)
        return data

    def test_fill_blank_persists_primary_and_multiple_accepted_answers_without_options(self):
        form = QuestionEditForm(
            self.payload(
                question_type=Question.Type.FILL_BLANK,
                correct_answer_text="Knowledge Tracing",
                accepted_answers_text="KT；知识追踪",
            ),
            instance=self.question,
        )

        self.assertTrue(form.is_valid(), form.errors)
        question = form.save()

        self.assertEqual(question.question_type, Question.Type.FILL_BLANK)
        self.assertEqual(question.correct_answer, "Knowledge Tracing")
        self.assertEqual(question.accepted_answers, ["KT", "知识追踪"])
        self.assertFalse(question.options.exists())

    def test_true_false_persists_the_fixed_incorrect_answer_as_json_false(self):
        form = QuestionEditForm(
            self.payload(
                question_type=Question.Type.TRUE_FALSE,
                correct_answer_text="错误",
            ),
            instance=self.question,
        )

        self.assertTrue(form.is_valid(), form.errors)
        question = form.save()

        self.assertEqual(question.question_type, Question.Type.TRUE_FALSE)
        self.assertIs(question.correct_answer, False)
        self.assertEqual(question.accepted_answers, [])
        self.assertFalse(question.options.exists())

    def test_converting_a_choice_question_to_fill_blank_clears_stale_options(self):
        QuestionOption.objects.create(
            question=self.question,
            key="A",
            text="遗留选项",
            display_order=1,
        )
        form = QuestionEditForm(
            self.payload(
                question_type=Question.Type.FILL_BLANK,
                correct_answer_text="标准答案",
                option_a="遗留选项",
            ),
            instance=self.question,
        )

        self.assertTrue(form.is_valid(), form.errors)
        question = form.save()

        self.assertEqual(question.question_type, Question.Type.FILL_BLANK)
        self.assertFalse(question.options.exists())

    def test_multiple_choice_save_uses_the_same_delimiters_as_validation(self):
        form = QuestionEditForm(
            self.payload(
                question_type=Question.Type.MULTIPLE,
                correct_answer_text="a；b",
                option_a="选项 A",
                option_b="选项 B",
            ),
            instance=self.question,
        )

        self.assertTrue(form.is_valid(), form.errors)
        question = form.save()

        self.assertEqual(question.correct_answer, ["A", "B"])

    def test_import_persists_explicit_fill_blank_accepted_answers_and_boolean_judgement(self):
        rows = parse_question_rows(
            [
                {
                    "sequence": 2,
                    "question": "KT 是什么？",
                    "question_type": "fill_blank",
                    "answer": "Knowledge Tracing",
                    "accepted_answers": "KT;知识追踪",
                },
                {
                    "sequence": 3,
                    "question": "KT 只用于教育测量。",
                    "question_type": "true_false",
                    "answer": "false",
                },
            ]
        )

        imported = import_questions(self.bank, rows)

        self.assertEqual(imported[0].accepted_answers, ["KT", "知识追踪"])
        self.assertIs(imported[1].correct_answer, False)

    def test_import_rolls_back_the_entire_new_type_batch_on_a_late_failure(self):
        rows = [
            ParsedQuestion(
                sequence=2,
                text="填空题",
                options={},
                answer="答案",
                question_type=Question.Type.FILL_BLANK,
            ),
            ParsedQuestion(
                sequence=2,
                text="判断题",
                options={},
                answer=True,
                question_type=Question.Type.TRUE_FALSE,
            ),
        ]

        with self.assertRaises(IntegrityError):
            import_questions(self.bank, rows)

        self.assertEqual(self.bank.questions.count(), 1)
