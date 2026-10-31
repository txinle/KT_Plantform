from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import User
from .models import Concept, Question, QuestionBank, QuestionConcept
from .services.scoring import score_payload


class WeightedScoringTests(SimpleTestCase):
    def test_weight_and_text_case_policy(self):
        self.assertEqual(score_payload('fill_blank', 'Paris', ' paris ', max_score=Decimal('2.50')), (True, Decimal('2.50')))
        self.assertEqual(score_payload('fill_blank', 'Paris', 'paris', max_score='2.50', case_sensitive=True), (False, Decimal('0')))
        self.assertEqual(score_payload('short_answer', 'New York', ' New\tYork ', case_sensitive=True), (True, Decimal('1')))
        self.assertEqual(score_payload('single_choice', 'A', 'a', case_sensitive=True, max_score='0.25'), (True, Decimal('0.25')))

    def test_invalid_weight_is_rejected(self):
        for value in ('NaN', 'Infinity', 0, -1, '1000', '0.001'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                score_payload('fill_blank', 'x', 'x', max_score=value)


class QuestionGroupFoundationTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='group-owner', role='teacher')
        self.bank = QuestionBank.objects.create(name='Groups', owner=self.teacher)
        self.concept = Concept.objects.create(owner=self.teacher, name='Vocabulary')

    def group(self, **changes):
        from .models import QuestionGroup
        return QuestionGroup.objects.create(bank=self.bank, kind='cloze', title='Story', material='A {{b01}} and {{b02}}.', **changes)

    def member(self, group, order, blank, **changes):
        question = Question.objects.create(bank=self.bank, group=group, group_order=order, blank_id=blank, sequence=order,
            question_type='fill_blank', text='', correct_answer='word', **changes)
        QuestionConcept.objects.create(question=question, concept=self.concept, is_primary=True)
        return question

    def test_material_segments_resolve_stable_member_keys_and_empty_stem_readiness(self):
        from .services.question_groups import validate_group_structure, material_segments, project_question_group
        from .services.question_readiness import readiness_issues
        group = self.group()
        first = self.member(group, 1, 'b01', max_score=Decimal('2.50'))
        second = self.member(group, 2, 'b02')
        validate_group_structure(group)
        self.assertEqual(material_segments(group), [
            {'kind': 'text', 'text': 'A '}, {'kind': 'blank', 'blank_id': 'b01', 'item_key': str(first.pk)},
            {'kind': 'text', 'text': ' and '}, {'kind': 'blank', 'blank_id': 'b02', 'item_key': str(second.pk)}, {'kind': 'text', 'text': '.'}])
        self.assertEqual(readiness_issues(first), [])
        projected = project_question_group(group)
        self.assertEqual(projected['total_max_score'], '3.50')
        self.assertEqual(projected['members'][0]['item_key'], str(first.pk))
        second.group = None
        self.assertTrue(readiness_issues(second))

    def test_structure_rejects_mismatched_markers_orders_types_and_bank(self):
        from .services.question_groups import validate_group_structure
        group = self.group()
        first = self.member(group, 1, 'b01')
        second = self.member(group, 2, 'b02')
        for field, value in [('blank_id', 'b01'), ('group_order', 3), ('question_type', 'single_choice')]:
            old = getattr(second, field)
            setattr(second, field, value)
            with self.subTest(field=field), self.assertRaises(ValidationError):
                validate_group_structure(group, [first, second])
            setattr(second, field, old)
        group.material += ' {{b01}}'
        with self.assertRaises(ValidationError):
            validate_group_structure(group, [first, second])

    def test_model_score_validation(self):
        question = Question(bank=self.bank, sequence=1, question_type='short_answer', text='Question', correct_answer='a')
        self.assertEqual(question.max_score, Decimal('1'))
        for score in ('0', '1000', 'NaN'):
            question.max_score = Decimal(score)
            with self.subTest(score=score), self.assertRaises(ValidationError):
                question.full_clean()
