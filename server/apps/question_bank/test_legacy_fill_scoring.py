from django.test import SimpleTestCase

from .services.scoring import score_payload


class LegacyFillScoringTests(SimpleTestCase):
    def test_equivalent_legacy_responses(self):
        for expected, response in [
            ('2；发出', '2，发出'), ('2；发出', '2   发出'),
            ('－1/2；6', '-0.5 6'), ('6；−4', '6; -4'),
            ('2∶1；2', '2：1,2'), ('−3', '-3'),
        ]:
            with self.subTest(response=response):
                self.assertTrue(score_payload('fill_blank', expected, response)[0])

    def test_missing_reordered_or_incorrect_blanks_are_not_correct(self):
        for response in ['-0.5', '6;-0.5', '0.5;6', '-0.5;;6', '-0.5;6;7']:
            self.assertFalse(score_payload('fill_blank', '－1/2；6', response)[0])

    def test_ordinary_text_and_short_answers_keep_exact_matching(self):
        self.assertFalse(score_payload('fill_blank', 'ice cream', 'ice;cream')[0])
        self.assertFalse(score_payload('short_answer', '2；发出', '2 发出')[0])
        self.assertFalse(score_payload('fill_blank', 'AB', 'ab', case_sensitive=True)[0])
        self.assertFalse(score_payload('fill_blank', 'a;b', 'a b', question_text='原文为____。')[0])

    def test_unit_is_only_accepted_when_the_stem_supplies_it(self):
        self.assertTrue(score_payload('fill_blank', '2；6', '2A;6V', question_text='I=____ A，U=____ V。')[0])
        self.assertFalse(score_payload('fill_blank', '2；6', '2V;6A', question_text='I=____ A，U=____ V。')[0])
        self.assertFalse(score_payload('fill_blank', '2；6', '2mA;6V', question_text='I=____ A，U=____ V。')[0])
