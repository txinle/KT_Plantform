import json

from django.test import TestCase

from apps.accounts.models import User
from apps.api.test_support import domain_payload
from .models import Concept, QuestionBank
from .test_group_import import cloze_rows, upload


class StandaloneGroupKCTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='standalone-group-kc', role='teacher')
        other = User.objects.create_user(username='standalone-other-kc', role='teacher')
        self.bank = QuestionBank.objects.create(owner=self.teacher, name='Standalone groups')
        self.own = Concept.objects.create(owner=self.teacher, name='English words')
        self.public = Concept.objects.create(scope='public', name='Public words')
        self.foreign = Concept.objects.create(owner=other, name='Other private words')
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/banks/{self.bank.pk}/'

    def rows(self, token=''):
        rows = cloze_rows()
        for row in rows:
            row['一级知识点'] = token
        return rows

    def preview(self, rows):
        response = self.client.post(self.base + 'imports/questions/preview/', {'file': upload(rows)})
        self.assertEqual(response.status_code, 200, response.content)
        return domain_payload(response)

    def confirm(self, preview):
        return self.client.post(self.base + 'imports/questions/confirm/', {'preview_id': preview['preview_id']}, content_type='application/json')

    def body(self, primary_id):
        return {'kind': 'cloze', 'title': 'Words', 'material': 'At {{b01}}.', 'members': [{
            'group_order': 1, 'blank_id': 'b01', 'question_type': 'fill_blank', 'text': '', 'answer': 'home',
            'primary_concept_id': primary_id, 'kc_mapping': {'nodes': []}, 'max_score': '2.50'}]}

    def test_group_without_primary_is_draft_and_private_primary_editor_makes_ready(self):
        response = self.client.post(self.base + 'groups/', self.body(None), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        group = domain_payload(response)
        self.assertEqual(group['status'], 'draft')
        self.assertEqual(group['members'][0]['status'], 'draft')
        ids = {row['id'] for row in group['available_concepts']}
        self.assertIn(self.own.pk, ids)
        self.assertIn(self.public.pk, ids)
        self.assertNotIn(self.foreign.pk, ids)
        body = {**group, 'expected_revision': group['revision']}
        body['members'][0]['primary_concept_id'] = self.own.pk
        response = self.client.put(self.base + f'groups/{group["id"]}/', body, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        result = domain_payload(response)
        self.assertEqual(result['status'], 'active')
        self.assertEqual(result['members'][0]['primary_concept_id'], self.own.pk)
        self.assertEqual(self.bank.questions.get().primary_concept, self.own)

    def test_editor_cannot_bind_another_owners_private_or_archived_concept(self):
        for primary_id in (self.foreign.pk, 999999):
            response = self.client.post(self.base + 'groups/', self.body(primary_id), content_type='application/json')
            self.assertEqual(response.status_code, 400, response.content)
        self.own.status = 'archived'
        self.own.save(update_fields=['status'])
        response = self.client.post(self.base + 'groups/', self.body(self.own.pk), content_type='application/json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(self.bank.groups.exists())

    def test_unified_import_resolves_own_names_and_explicit_ids_and_deduplicates(self):
        rows = self.rows(self.own.name)
        rows[1]['一级知识点'] = f'id:{self.own.pk}'
        preview = self.preview(rows)
        self.assertEqual(preview['preview']['groups'][0]['classification'], 'ready')
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.bank.questions.count(), 2)
        self.assertTrue(all(question.primary_concept == self.own for question in self.bank.questions.all()))
        self.assertEqual(self.preview(rows)['preview']['groups'][0]['classification'], 'duplicate')
        missing = self.preview(self.rows())
        self.assertEqual(missing['preview']['groups'][0]['classification'], 'draft')
        foreign = self.preview(self.rows(f'id:{self.foreign.pk}'))
        self.assertEqual(foreign['preview']['groups'][0]['classification'], 'draft')
        self.assertTrue(foreign['preview']['groups'][0]['errors'])
