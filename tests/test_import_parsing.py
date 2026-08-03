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

    def test_kc_names_are_split_and_normalized(self):
        rows = [{'题目序号': 1, '主知识点': ' 一元一次方程 ', '次知识点': '移项； 等式性质'}]
        parsed = parse_kc_rows(rows)
        self.assertEqual(parsed[0].primary, '一元一次方程')
        self.assertEqual(parsed[0].secondary, ['移项', '等式性质'])


if __name__ == '__main__':
    unittest.main()
