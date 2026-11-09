from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Course, User
from apps.api.test_support import domain_payload
from .models import Concept, ImportPreviewBatch, QuestionBank
from .services.question_images import canonical_image, image_keys, with_question_images
from .test_question_images import image_bytes
from .test_unified_question_import import question_row, upload_rows


class QuestionImageImportApiTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.directory.name))
        override.enable()
        self.addCleanup(override.disable)
        self.teacher = User.objects.create_user(username='image-import-owner', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='图片导入', code='IMAGE', term='2026')
        self.bank = QuestionBank.objects.create(course=self.course, created_by=self.teacher, name='课程题库')
        for sequence in range(1, 4):
            Concept.objects.create(course=self.course, code=f'KC{sequence}', name=f'知识点{sequence}')
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/imports/questions/'
        self.raw_image = image_bytes()
        self.key, self.uri = canonical_image(self.raw_image, 'diagram.png')

    def preview(self, *, attachment=True, reference='diagram.png'):
        row = question_row(题目='观察电路图。\n{{image:' + reference + '}}', 题型='单选题',
                           选项A='文字选项', 选项B='{{image:' + reference + '}}', 答案='B')
        data = {'file': upload_rows([row])}
        if attachment:
            data['images'] = [SimpleUploadedFile('diagram.png', self.raw_image, content_type='image/png')]
        return self.client.post(self.base + 'preview/', data)

    def test_csv_image_attachment_is_staged_previewed_and_confirmed_through_existing_api(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.content)
        result = domain_payload(response)
        row = result['preview']['rows'][0]
        self.assertEqual(row['classification'], 'ready', row['errors'])
        self.assertEqual(row.get('images'), {self.key: self.uri})
        self.assertEqual(image_keys(row['text']), {self.key})
        self.assertEqual(image_keys(row['options'][1]['text']), {self.key})
        self.assertFalse(self.bank.questions.exists())
        self.assertFalse((Path(self.directory.name) / 'question-images').exists())
        self.assertIn(self.key, str(ImportPreviewBatch.objects.get(pk=result['preview_id']).payload))
        confirmed = self.client.post(self.base + 'confirm/', {'preview_id': result['preview_id']}, content_type='application/json')
        self.assertEqual(confirmed.status_code, 201, confirmed.content)
        question = self.bank.questions.get()
        self.assertEqual(question.status, 'active')
        self.assertEqual(image_keys(question.options.get(key='B').text), {self.key})
        self.assertEqual(with_question_images({'text': question.text})['images'], {self.key: self.uri})
        editor_url = self.base.replace('imports/questions/', f'questions/{question.pk}/editor/')
        editor = self.client.get(editor_url + '?readonly=1')
        self.assertEqual(editor.status_code, 200)
        self.assertEqual(domain_payload(editor)['question']['images'], {self.key: self.uri})
        repeated = self.client.post(self.base + 'confirm/', {'preview_id': result['preview_id']}, content_type='application/json')
        self.assertEqual(repeated.status_code, 201)
        self.assertEqual(self.bank.questions.count(), 1)

    def test_group_material_retains_both_images_and_interactive_blanks(self):
        from .test_group_import import cloze_rows, upload
        rows = cloze_rows()
        rows[0]['共享材料'] += ' {{image:map.png}}'
        response = self.client.post(self.base + 'preview/', {
            'file': upload(rows), 'images': SimpleUploadedFile('map.png', self.raw_image),
        })
        self.assertEqual(response.status_code, 200, response.content)
        preview = domain_payload(response)
        group = preview['preview']['groups'][0]
        self.assertEqual(group['classification'], 'ready', group['errors'])
        self.assertEqual(group['images'], {self.key: self.uri})
        self.assertEqual([s['blank_id'] for s in group['material_segments'] if s['kind'] == 'blank'], ['b01', 'b02'])
        confirmed = self.client.post(self.base + 'confirm/', {'preview_id': preview['preview_id']}, content_type='application/json')
        self.assertEqual(confirmed.status_code, 201, confirmed.content)
        detail = self.client.get(self.base.replace('imports/questions/', f'groups/{self.bank.groups.get().pk}/'))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(domain_payload(detail)['images'], {self.key: self.uri})

    def test_missing_attachment_is_reported_and_not_confirmable(self):
        response = self.preview(attachment=False)
        self.assertEqual(response.status_code, 200, response.content)
        result = domain_payload(response)
        row = result['preview']['rows'][0]
        self.assertEqual(row['classification'], 'invalid')
        self.assertTrue(row['errors'])
        self.assertFalse(result['preview']['confirmable'])
        self.assertFalse(self.bank.questions.exists())

    def test_a_raw_stored_image_identifier_cannot_read_another_imports_picture(self):
        from .services.question_images import persist_image_assets
        persist_image_assets({self.key: self.uri})
        response = self.preview(attachment=False, reference=self.key)
        self.assertEqual(response.status_code, 200, response.content)
        row = domain_payload(response)['preview']['rows'][0]
        self.assertEqual(row['classification'], 'invalid')
        self.assertNotIn(self.key, row.get('images', {}))
