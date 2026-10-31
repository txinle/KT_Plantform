from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .question_group_publication import frozen_question_fields


class FrozenEnglishContentTests(SimpleTestCase):
    def question(self, **changes):
        return SimpleNamespace(max_score=2, content_language='en',
            text_match_case_sensitive=False, text_match_policy_version='text-exact-v1',
            group_id=None, group_order=None, blank_id='', educational_type='standard', title='', instructions='', **changes)

    def test_writing_subtype_is_frozen_without_changing_response_type(self):
        question = self.question()
        question.educational_type = 'essay'
        question.question_type = 'short_answer'
        fields = frozen_question_fields(None, question)
        self.assertEqual(fields['educational_type'], 'essay')
        self.assertEqual(question.question_type, 'short_answer')

    def test_shared_word_bank_is_in_the_frozen_group_content(self):
        question = self.question()
        question.group_id = 8
        question.group = SimpleNamespace(pk=8, revision=2, kind='words', title='A day',
            instructions='Choose words', material='At {{b01}}.', content_language='en',
            word_bank=['home', 'school'])
        with patch('apps.experiments.question_group_publication.QuestionGroupSnapshot.objects.get_or_create') as create:
            create.return_value = (SimpleNamespace(pk=4), True)
            fields = frozen_question_fields(None, question)
        self.assertEqual(create.call_args.kwargs['defaults']['word_bank'], ['home', 'school'])
        self.assertEqual(fields['group_snapshot'].pk, 4)
