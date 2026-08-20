from decimal import Decimal
import unittest

from apps.question_bank.services.scoring import score_payload


class ScoringTests(unittest.TestCase):
    def test_single_choice_is_case_insensitive(self):
        self.assertEqual(score_payload('single_choice', 'C', 'c'), (True, Decimal('1')))

    def test_multiple_choice_compares_sets(self):
        self.assertEqual(score_payload('multiple_choice', ['A', 'C'], ['C', 'A']), (True, Decimal('1')))
        self.assertEqual(score_payload('multiple_choice', ['A', 'C'], ['A']), (False, Decimal('0')))

    def test_numeric_accepts_absolute_tolerance(self):
        self.assertEqual(
            score_payload('numeric', '3.14', '3.145', absolute_tolerance='0.01'),
            (True, Decimal('1')),
        )

    def test_short_answer_normalizes_whitespace_and_case(self):
        self.assertEqual(
            score_payload('short_answer', 'Knowledge Tracing', '  knowledge   tracing  '),
            (True, Decimal('1')),
        )

    def test_fill_blank_matches_after_trimming_casefolding_and_collapsing_internal_whitespace(self):
        self.assertEqual(
            score_payload(
                'fill_blank',
                'Knowledge Tracing',
                '  knowledge\t  tracing  ',
                accepted_answers=['KT'],
            ),
            (True, Decimal('1')),
        )
        self.assertEqual(
            score_payload('fill_blank', 'Knowledge Tracing', 'knowledge-tracing'),
            (False, Decimal('0')),
        )

    def test_true_false_normalizes_fixed_correct_and_incorrect_answers_to_booleans(self):
        self.assertEqual(score_payload('true_false', True, '正确'), (True, Decimal('1')))
        self.assertEqual(score_payload('true_false', False, 'false'), (True, Decimal('1')))
        self.assertEqual(score_payload('true_false', True, '错误'), (False, Decimal('0')))
        self.assertEqual(score_payload('true_false', True, 'yes'), (False, Decimal('0')))


if __name__ == '__main__':
    unittest.main()
