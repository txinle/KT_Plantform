import unittest

from apps.question_bank.services.import_parsing import parse_kc_rows, parse_question_rows


class ImportParsingTests(unittest.TestCase):
    def test_minimal_question_headers_are_parsed(self):
        rows = [{
            '序号': 1,
            '题目': '2+2=?',
            '选项A': '3',
            '选项B': '4',
            '选项C': '',
            '选项D': '',
            '选项E': '',
            '选项F': '',
            '答案': 'B',
        }]
        parsed = parse_question_rows(rows)
        self.assertEqual(parsed[0].question_type, 'single_choice')
        self.assertEqual(parsed[0].options, {'A': '3', 'B': '4'})

    def test_multiple_choice_is_inferred(self):
        rows = [{'序号': 2, '题目': '质数', '选项A': '2', '选项B': '4', '选项C': '5', '答案': 'A,C'}]
        self.assertEqual(parse_question_rows(rows)[0].question_type, 'multiple_choice')

    def test_explicit_chinese_and_english_question_types_support_fill_blank_and_true_false(self):
        parsed = parse_question_rows([
            {
                'sequence': 3,
                'question': 'KT 的中文名称是？',
                'question_type': 'fill_blank',
                'answer': '知识追踪',
                'accepted_answers': '知识跟踪；KT',
            },
            {
                '序号': 4,
                '题干': '所有学习者都应获得相同题集。',
                '题型': '判断题',
                '答案': '错误',
            },
        ])

        self.assertEqual(parsed[0].question_type, 'fill_blank')
        self.assertEqual(parsed[0].accepted_answers, ['知识跟踪', 'KT'])
        self.assertEqual(parsed[1].question_type, 'true_false')
        self.assertIs(parsed[1].answer, False)

    def test_explicit_question_type_rejects_unknown_type_illegal_judgement_and_missing_choice_option(self):
        cases = [
            ({'sequence': 1, 'question': 'x', 'question_type': 'essay', 'answer': 'x'}, '未知题型'),
            ({'sequence': 1, 'question': 'x', 'question_type': 'true_false', 'answer': '不确定'}, '判断题答案'),
            ({'sequence': 1, 'question': 'x', 'question_type': 'true_false', 'answer': '1'}, '判断题答案'),
            (
                {
                    'sequence': 1,
                    'question': 'x',
                    'question_type': 'single_choice',
                    'option_a': 'A',
                    'answer': 'B',
                },
                '已有的 A-F 选项',
            ),
        ]

        for row, error in cases:
            with self.subTest(row=row):
                with self.assertRaisesRegex(ValueError, error):
                    parse_question_rows([row])

    def test_explicit_non_choice_type_with_options_rejects_the_entire_batch(self):
        valid_first_row = {
            "sequence": 1,
            "question": "先前有效的填空题",
            "question_type": "fill_blank",
            "answer": "答案",
        }
        cases = (
            ("numeric", "1"),
            ("fill_blank", "答案"),
            ("true_false", "正确"),
            ("short_answer", "答案"),
        )

        for question_type, answer in cases:
            with self.subTest(question_type=question_type):
                rows = [
                    valid_first_row,
                    {
                        "sequence": 2,
                        "question": "不应接受选项的题目",
                        "question_type": question_type,
                        "answer": answer,
                        "option_a": "残留选项",
                    },
                ]

                with self.assertRaisesRegex(ValueError, "非选择题不能填写选项"):
                    parse_question_rows(rows)

    def test_explicit_numeric_type_rejects_nonfinite_answers(self):
        for answer in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(answer=answer):
                with self.assertRaisesRegex(ValueError, "数值题答案必须是有效数字"):
                    parse_question_rows(
                        [
                            {
                                "sequence": 1,
                                "question": "数值题",
                                "question_type": "numeric",
                                "answer": answer,
                            }
                        ]
                    )

    def test_legacy_numeric_inference_rejects_nonfinite_answers(self):
        for answer in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(answer=answer):
                with self.assertRaisesRegex(ValueError, "数值题答案必须是有效数字"):
                    parse_question_rows(
                        [
                            {
                                "sequence": 1,
                                "question": "旧式数值题",
                                "answer": answer,
                            }
                        ]
                    )

    def test_legacy_rows_without_a_type_keep_choice_and_numeric_inference(self):
        parsed = parse_question_rows(
            [
                {
                    "sequence": 1,
                    "question": "旧式单选",
                    "option_a": "选项 A",
                    "option_b": "选项 B",
                    "answer": "B",
                },
                {
                    "sequence": 2,
                    "question": "旧式数值",
                    "answer": "3.5",
                },
            ]
        )

        self.assertEqual(
            [item.question_type for item in parsed],
            ["single_choice", "numeric"],
        )

    def test_kc_names_are_split_and_normalized(self):
        rows = [{'题目序号': 1, '主知识点': ' 一元一次方程 ', '次知识点': '移项； 等式性质'}]
        parsed = parse_kc_rows(rows)
        self.assertEqual(parsed[0].primary, '一元一次方程')
        self.assertEqual(parsed[0].secondary, ['移项', '等式性质'])


if __name__ == '__main__':
    unittest.main()
