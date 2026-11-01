import json
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from apps.accounts.models import Course, User
from apps.api.test_support import domain_payload
from .models import Concept, Question, QuestionBank


class CompleteQuestionEditorTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='complete-editor', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='English', code='COMPLETE', term='2026')
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='Complete questions')
        Concept.objects.create(course=self.course, code='KC1', name='Vocabulary')
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/'

    def request_json(self, method, url, body):
        return getattr(self.client, method)(url, json.dumps(body), content_type='application/json')

    def create(self):
        body = {'kind': 'cloze', 'title': 'Cities', 'material': 'Visit {{b01}} and {{b02}}.', 'content_language': 'en',
            'members': [{'group_order': order, 'blank_id': f'b{order:02}', 'question_type': 'fill_blank', 'text': '',
                'answer': answer, 'max_score': '2.50', 'accepted_answers': [],
                'kc_mapping': {'root_kc_code': 'KC1', 'nodes': [{'kc_code': 'KC1', 'parent_kc_code': None, 'order': 1}]}}
                for order, answer in [(1, 'Paris'), (2, 'London')]]}
        response = self.request_json('post', self.base + 'groups/', body)
        self.assertEqual(response.status_code, 201, response.content)
        return domain_payload(response)

    def test_complete_collection_search_and_type_include_groups_once(self):
        group = self.create()
        Question.objects.create(bank=self.bank, sequence=3, text='Ordinary', question_type='short_answer', correct_answer='', status='draft')
        response = domain_payload(self.client.get(self.base + 'questions/?complete_only=1'))
        self.assertEqual(response['page']['total_items'], 2)
        self.assertEqual({row['unit_key'] for row in response['items']}, {f'group:{group["id"]}', f'question:{self.bank.questions.get(group=None).pk}'})
        result = domain_payload(self.client.get(self.base + 'questions/?complete_only=1&question_type=cloze&q=Visit'))
        self.assertEqual(result['page']['total_items'], 1)
        self.assertEqual(len(result['items'][0]['member_kc_mappings']), 2)
        self.assertEqual(domain_payload(self.client.get(self.base + 'questions/?complete_only=1&status=draft'))['page']['total_items'], 1)

    def test_complete_collection_projects_only_the_requested_page(self):
        from .services.question_kc_mapping import project_question_kc_mapping
        for sequence in range(1, 46):
            Question.objects.create(bank=self.bank, sequence=sequence, text=f'Ordinary {sequence}',
                question_type='short_answer', correct_answer='Answer', status='draft')
        with patch('apps.question_bank.services.complete_questions.project_question_kc_mapping',
                   wraps=project_question_kc_mapping) as project, CaptureQueriesContext(connection) as queries:
            result = domain_payload(self.client.get(self.base + 'questions/?complete_only=1&page=2'))
        self.assertEqual(result['page']['total_items'], 45)
        self.assertEqual([row['sequence'] for row in result['items']], list(range(21, 41)))
        self.assertEqual(project.call_count, 20)
        self.assertLess(len(queries), 35)

    def test_bank_list_summary_does_not_project_member_details(self):
        self.create()
        with patch('apps.question_bank.services.question_groups.project_question_group',
                   side_effect=AssertionError('List summary must not generate full group documents')):
            result = domain_payload(self.client.get(f'/api/v1/teacher/courses/{self.course.pk}/banks/'))
        summary = result['items'][0]['unit_summary']
        self.assertEqual((summary['complete_count'], summary['response_item_count'], summary['usable_count']), (1, 2, 1))

    def test_group_detail_queries_are_batched_and_readiness_stays_live(self):
        for _ in range(5):
            self.create()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.content)
        result = domain_payload(response)
        self.assertEqual(len(result['groups']), 5)
        self.assertEqual(result['unit_summary']['usable_count'], 5)
        self.assertLess(len(queries), 40)
        Concept.objects.filter(course=self.course).update(status='archived')
        result = domain_payload(self.client.get(self.base))
        self.assertEqual(result['unit_summary']['usable_count'], 0)
        self.assertTrue(all(group['status'] == 'draft' for group in result['groups']))

    def test_mixed_pagination_and_search_preserve_whole_groups(self):
        from .services.question_groups import project_question_group
        for sequence in range(1, 21):
            Question.objects.create(bank=self.bank, sequence=sequence, text=f'Ordinary {sequence}',
                question_type='short_answer', correct_answer='', status='draft')
        group = self.create()
        with patch('apps.question_bank.services.complete_questions.project_question_group',
                   wraps=project_question_group) as project:
            result = domain_payload(self.client.get(self.base + 'questions/?complete_only=1&page=2'))
        self.assertEqual(result['page']['total_items'], 21)
        self.assertEqual([row['unit_key'] for row in result['items']], [f'group:{group["id"]}'])
        self.assertEqual(len(result['items'][0]['member_kc_mappings']), 2)
        self.assertEqual(project.call_count, 1)
        for query in ('Vocabulary', 'KC1', 'Cities'):
            result = domain_payload(self.client.get(self.base + 'questions/', {'complete_only': '1', 'q': query}))
            self.assertEqual([row['unit_key'] for row in result['items']], [f'group:{group["id"]}'])
        result = domain_payload(self.client.get(self.base + 'questions/?complete_only=1&status=active'))
        self.assertEqual(result['page']['total_items'], 1)
        Concept.objects.filter(course=self.course).update(status='archived')
        result = domain_payload(self.client.get(self.base + 'questions/?complete_only=1&status=draft&page=2'))
        self.assertEqual(result['page']['total_items'], 21)
        self.assertEqual(result['items'][0]['status'], 'draft')

    def test_batched_group_read_does_not_mask_a_foreign_bank_member(self):
        group = self.create()
        other_bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='Other')
        Question.objects.filter(pk=group['members'][0]['id']).update(status='draft')
        Question.objects.filter(pk=group['members'][0]['id']).update(bank=other_bank)
        result = domain_payload(self.client.get(self.base))
        self.assertEqual(result['groups'][0]['status'], 'draft')
        self.assertIn('题组成员必须属于同一题库。', result['groups'][0]['readiness_issues'])

    def test_create_and_conflict_preserve_original(self):
        url = self.base + 'questions/0/editor/'
        self.assertEqual(self.client.get(url).status_code, 200)
        body = {'text': 'New', 'question_type': 'numeric', 'status': 'active', 'correct_answer_text': '3', 'absolute_tolerance': '0.1'}
        created = self.request_json('post', url, body)
        self.assertEqual(created.status_code, 201, created.content)
        question = domain_payload(created)['question']
        update_url = self.base + f'questions/{question["id"]}/editor/'
        self.assertEqual(self.request_json('post', update_url, body).status_code, 405)
        self.assertEqual(self.bank.questions.count(), 1)
        body.update(expected_updated_at=question['updated_at'], text='First')
        first = self.request_json('put', update_url, body)
        self.assertEqual(first.status_code, 200, first.content)
        body['text'] = 'Stale'
        self.assertEqual(self.request_json('put', update_url, body).status_code, 409)
        self.assertEqual(Question.objects.get(pk=question['id']).text, 'First')

    def test_new_question_mapping_is_validated_without_partial_creation(self):
        body = {'text': 'Invalid mapping', 'question_type': 'short_answer', 'status': 'active',
            'kc_mapping_json': json.dumps({'root_kc_code': 'foreign', 'nodes': [{'kc_code': 'foreign', 'parent_kc_code': None, 'order': 1}]})}
        response = self.request_json('post', self.base + 'questions/0/editor/', body)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(self.bank.questions.exists())

    def test_archived_readonly_preview_and_standalone_complete_collection(self):
        question = Question.objects.create(bank=self.bank, sequence=1, text='Read only', question_type='short_answer', correct_answer='', status='draft')
        self.bank.visibility = 'archived'
        self.bank.save(update_fields=['visibility'])
        url = self.base + f'questions/{question.pk}/editor/'
        self.assertEqual(self.client.get(url + '?readonly=1').status_code, 200)
        self.assertEqual(self.request_json('put', url, {'text': 'No'}).status_code, 403)
        standalone = QuestionBank.objects.create(owner=self.teacher, name='Independent')
        self.base = f'/api/v1/teacher/banks/{standalone.pk}/'
        result = self.request_json('post', self.base + 'questions/0/editor/', {'text': 'Standalone', 'question_type': 'numeric', 'correct_answer_text': '5', 'status': 'active'})
        self.assertEqual(result.status_code, 201, result.content)
        existing_url = self.base + f'questions/{domain_payload(result)["question"]["id"]}/editor/'
        self.assertEqual(self.request_json('post', existing_url, {'text': 'Duplicate'}).status_code, 405)
        self.assertEqual(standalone.questions.count(), 1)
        response = domain_payload(self.client.get(self.base + '?complete_only=1&q=Standalone'))
        self.assertEqual(response['question_page']['total_items'], 1)
        self.assertEqual(response['questions'][0]['edit_url'], f'/teacher/banks/{standalone.pk}/questions/{domain_payload(result)["question"]["id"]}/edit/')
