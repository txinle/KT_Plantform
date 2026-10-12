from decimal import Decimal

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

    def test_numeric_nonfinite_answers_remain_unavailable_drafts(self):
        for answer in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(answer=answer):
                form = QuestionEditForm(
                    self.payload(
                        question_type=Question.Type.NUMERIC,
                        correct_answer_text=answer,
                    ),
                    instance=self.question,
                )

                self.assertTrue(form.is_valid(), form.errors)
                question = form.save()
                self.assertEqual(question.status, Question.Status.DRAFT)
                self.assertEqual(question.correct_answer, answer)
                from .services.question_readiness import readiness_issues
                self.assertTrue(any("数值题答案" in issue for issue in readiness_issues(question)))

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

    def test_import_persists_explanation_and_teacher_difficulty_from_normalized_headers(self):
        rows = parse_question_rows(
            [
                {
                    "序号": 2,
                    "题目": "2 + 2 等于多少？",
                    "题型": "单选题",
                    "选项A": "3",
                    "选项B": "4",
                    "答案": "B",
                    "解析 ": "  2 与 2 相加得到 4。  ",
                    "难度": "0.350",
                }
            ]
        )

        imported = import_questions(self.bank, rows)

        self.assertEqual(imported[0].explanation, "2 与 2 相加得到 4。")
        self.assertEqual(imported[0].teacher_difficulty, Decimal("0.350"))

    def test_import_keeps_blank_difficulty_unset(self):
        rows = parse_question_rows(
            [{"序号": 2, "题目": "判断", "题型": "判断题", "答案": "正确", "难度": ""}]
        )

        imported = import_questions(self.bank, rows)

        self.assertIsNone(imported[0].teacher_difficulty)

    def test_import_rejects_invalid_teacher_difficulty(self):
        for value in ("-0.001", "1.001", "0.1234", "NaN", "Infinity", "35%", "困难"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "难度必须为 0 到 1 之间且最多三位小数",
            ):
                parse_question_rows(
                    [{"序号": 2, "题目": "判断", "题型": "判断题", "答案": "正确", "难度": value}]
                )

    def test_blank_sequences_are_generated_after_bank_and_explicit_maximum_in_file_order(self):
        rows = parse_question_rows(
            [
                {"序号": "", "题目": "第一题", "题型": "判断题", "答案": "正确"},
                {"序号": "20", "题目": "第二题", "题型": "判断题", "答案": "正确"},
                {"序号": "", "题目": "第三题", "题型": "判断题", "答案": "错误"},
            ],
            sequence_floor=10,
        )

        self.assertEqual([row.sequence for row in rows], [21, 20, 22])

    def test_multiple_choice_can_read_additional_correct_keys_from_accepted_answer_column(self):
        rows = parse_question_rows(
            [
                {
                    "序号": "",
                    "题目": "请选择两项",
                    "题型": "多选题",
                    "选项A": "甲",
                    "选项B": "乙",
                    "选项C": "丙",
                    "答案": "A",
                    "可接受答案": "C",
                }
            ]
        )

        self.assertEqual(rows[0].answer, ["A", "C"])
        self.assertEqual(rows[0].accepted_answers, [])

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

        with self.assertRaisesRegex(ValueError, "导入批次包含重复序号"):
            import_questions(self.bank, rows)

        self.assertEqual(self.bank.questions.count(), 1)
