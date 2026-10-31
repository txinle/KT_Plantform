import json

from django.test import TestCase

from apps.accounts.models import Course, User
from apps.api.test_support import domain_payload
from .models import Concept, QuestionBank


class GroupManagementApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='group-edit', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='English', code='EDIT', term='2026')
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='English')
        Concept.objects.create(course=self.course, code='KC1', name='Vocabulary')
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/'

    def body(self):
        return {'kind': 'cloze', 'title': 'Cities', 'material': 'Visit {{b01}} and {{b02}}.', 'instructions': 'Fill in.', 'content_language': 'en',
            'members': [{'id': None, 'group_order': order, 'blank_id': f'b{order:02}', 'text': '', 'question_type': 'fill_blank',
                'answer': answer, 'accepted_answers': [], 'max_score': '2.50', 'text_match_case_sensitive': True,
                'kc_mapping': {'root_kc_code': 'KC1', 'nodes': [{'kc_code': 'KC1', 'parent_kc_code': None, 'order': 1}]}}
                for order, answer in [(1, 'Paris'), (2, 'London')]]}

    def request_json(self, method, url, body):
        return getattr(self.client, method)(url, json.dumps(body), content_type='application/json')

    def create(self):
        response = self.request_json('post', self.base + 'groups/', self.body())
        self.assertEqual(response.status_code, 201, response.content)
        return domain_payload(response)

    def test_course_group_create_update_revision_and_stable_member_identity(self):
        group = self.create()
        self.assertEqual(group['total_max_score'], '5.00')
        self.assertEqual(group['status'], 'active')
        ids = [member['id'] for member in group['members']]
        stable = list(self.bank.questions.values_list('course_identity__stable_code', flat=True))
        body = {**group, 'expected_revision': group['revision'], 'title': 'Updated'}
        body['members'].reverse()
        body['members'][0]['group_order'] = 1
        body['members'][1]['group_order'] = 2
        updated = self.request_json('put', self.base + f'groups/{group["id"]}/', body)
        self.assertEqual(updated.status_code, 200, updated.content)
        result = domain_payload(updated)
        self.assertEqual(result['revision'], 2)
        self.assertEqual([member['id'] for member in result['members']], list(reversed(ids)))
        self.assertEqual(list(self.bank.questions.values_list('course_identity__stable_code', flat=True)), stable)
        stale = self.request_json('put', self.base + f'groups/{group["id"]}/', body)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(self.client.get(self.base + f'groups/{group["id"]}/').status_code, 200)

    def test_member_deletion_requires_explicit_confirmation_and_updates_whole_material(self):
        group = self.create()
        body = {**group, 'expected_revision': 1, 'members': group['members'][:1], 'material': 'Visit {{b01}}.'}
        url = self.base + f'groups/{group["id"]}/'
        self.assertEqual(self.request_json('put', url, body).status_code, 400)
        self.assertEqual(self.bank.questions.count(), 2)
        body['confirm_member_deletion'] = True
        result = self.request_json('put', url, body)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.bank.questions.count(), 1)
        self.assertEqual(self.request_json('delete', url, {'confirmation': 'delete'}).status_code, 204)
        self.assertFalse(self.bank.questions.exists())

    def test_partial_legacy_editor_and_delete_are_redirected_to_group_editor(self):
        group = self.create()
        member = group['members'][0]
        response = self.request_json('put', self.base + f'questions/{member["id"]}/editor/', {'text': 'Break', 'question_type': 'short_answer', 'status': 'draft'})
        self.assertEqual(response.status_code, 409, response.content)
        deletion = self.request_json('delete', self.base + f'questions/{member["id"]}/delete/', {'confirmation': 'delete'})
        self.assertEqual(deletion.status_code, 409, deletion.content)
        self.assertEqual(self.bank.questions.count(), 2)

    def test_structure_and_foreign_members_rejected_atomically(self):
        group = self.create()
        for changes in ({'kind': 'reading'}, {'material': 'Missing blanks'}, {'members': [{**group['members'][0], 'id': 999999}]}):
            body = {**group, 'expected_revision': 1, **changes}
            response = self.request_json('put', self.base + f'groups/{group["id"]}/', body)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.bank.groups.get().revision, 1)

    def test_standalone_group_api_copy_and_bank_projection(self):
        from .services.import_service import copy_shared_bank
        bank = QuestionBank.objects.create(owner=self.teacher, name='Standalone', visibility='shared')
        self.bank = bank
        self.base = f'/api/v1/teacher/banks/{bank.pk}/'
        body = self.body()
        for member in body['members']:
            member['kc_mapping'] = {'nodes': []}
        response = self.request_json('post', self.base + 'groups/', body)
        self.assertEqual(response.status_code, 201, response.content)
        detail = domain_payload(self.client.get(self.base))
        self.assertEqual(len(detail['groups']), 1)
        copied = copy_shared_bank(bank, self.teacher)
        self.assertEqual(copied.groups.count(), 1)
        self.assertEqual(copied.groups.get().members.count(), 2)
        self.assertEqual(list(copied.questions.values_list('max_score', flat=True)), [2.5, 2.5])

    def test_authorization_and_archived_content_guards(self):
        group = self.create()
        outsider = User.objects.create_user(username='group-outsider', role='teacher')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.base + f'groups/{group["id"]}/').status_code, 404)
        self.client.force_login(self.teacher)
        self.bank.visibility = 'archived'
        self.bank.save(update_fields=['visibility'])
        response = self.request_json('put', self.base + f'groups/{group["id"]}/', {**group, 'expected_revision': 1})
        self.assertEqual(response.status_code, 403)

    def test_snapshot_reference_blocks_group_deletion(self):
        from apps.experiments.models import Experiment, ExperimentVersion, QuestionSnapshot
        group = self.create()
        experiment = Experiment.objects.create(course=self.course, name='Frozen', created_by=self.teacher)
        version = ExperimentVersion.objects.create(experiment=experiment, version_number=1, published_by=self.teacher)
        QuestionSnapshot.objects.create(version=version, source_question=self.bank.questions.first(), source_sequence=1, question_type='fill_blank', text='', correct_answer='Paris')
        response = self.request_json('delete', self.base + f'groups/{group["id"]}/', {'confirmation': 'delete'})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(self.bank.questions.count(), 2)

    def test_ordinary_editor_saves_weight_and_matching_in_both_scopes(self):
        from .models import Question
        for bank, base in [(self.bank, self.base), (QuestionBank.objects.create(owner=self.teacher, name='Standalone'), None)]:
            base = base or f'/api/v1/teacher/banks/{bank.pk}/'
            question = Question.objects.create(bank=bank, sequence=1, question_type='fill_blank', text='City?', correct_answer='Paris', status='draft')
            response = self.request_json('put', base + f'questions/{question.pk}/editor/', {
                'text': 'City?', 'question_type': 'fill_blank', 'status': 'draft', 'correct_answer_text': 'Paris',
                'max_score': '3.25', 'text_match_case_sensitive': True, 'content_language': 'en', 'text_match_policy_version': 'text-exact-v1'})
            self.assertEqual(response.status_code, 200, response.content)
            question.refresh_from_db()
            self.assertEqual(str(question.max_score), '3.25')
            self.assertTrue(question.text_match_case_sensitive)
            self.assertEqual(domain_payload(response)['question']['max_score'], '3.25')
