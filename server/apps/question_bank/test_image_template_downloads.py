from io import BytesIO
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase
from django.urls import Resolver404, resolve

from apps.accounts.models import User
from .services.unified_reader import read_unified_rows


class ImageTemplateDownloadRoutingTests(SimpleTestCase):
    def test_companion_zip_is_a_registered_teacher_download(self):
        try:
            match = resolve('/api/v1/teacher/import-templates/question-images.zip')
        except Resolver404:
            match = None
        self.assertIsNotNone(match)

    def test_default_csv_download_contains_the_image_question_example(self):
        from .downloads import question_import_template
        request = RequestFactory().get('/api/v1/teacher/import-templates/questions.csv')
        request.user = User(username='teacher', role='teacher')
        response = question_import_template(request)
        content = b''.join(response.streaming_content) if response.streaming else response.content
        response.close()
        self.assertIn(b'{{image:image3.png}}', content)


class ImageTemplateDownloadTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='image-template-teacher', role='teacher')
        self.client.force_login(self.teacher)

    def download(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content) if response.streaming else response.content
        response.close()
        return response, content

    def test_actual_downloads_in_all_three_formats_roundtrip_with_image_companion(self):
        response, content = self.download('/api/v1/teacher/import-templates/question-images.zip')
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('question_import_images.zip', response['Content-Disposition'])
        with ZipFile(BytesIO(content)) as archive:
            image_bytes = {name: archive.read(name) for name in archive.namelist() if name.endswith('.png')}
            self.assertEqual(len(image_bytes), 4)
            self.assertIn('同时', archive.read('README.txt').decode('utf-8-sig'))
        for family, count in [('questions', 6), ('question-groups', 24)]:
            first_rows = []
            for extension in ('csv', 'xlsx', 'docx'):
                with self.subTest(family=family, extension=extension):
                    _, content = self.download(f'/api/v1/teacher/import-templates/{family}.{extension}')
                    attachments = [SimpleUploadedFile(name, data) for name, data in image_bytes.items()] if extension == 'csv' else []
                    rows = read_unified_rows(SimpleUploadedFile('questions.' + extension, content), image_files=attachments)
                    self.assertEqual(len(rows), count)
                    self.assertTrue(all(not row['_errors'] for row in rows), rows[0]['_errors'])
                    self.assertEqual(len(rows[0]['_image_assets']), 4)
                    self.assertTrue(all(rows[0]['选项' + key].startswith('{{image:') for key in 'ABCD'))
                    first_rows.append({field: rows[0][field] for field in ('题目', '选项A', '选项B', '选项C', '选项D', '答案')})
            self.assertEqual(first_rows[0], first_rows[1])
            self.assertEqual(first_rows[0], first_rows[2])

    def test_image_companion_requires_teacher_or_admin(self):
        url = '/api/v1/teacher/import-templates/question-images.zip'
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 401)
        student = User.objects.create_user(username='image-template-student', role='student')
        self.client.force_login(student)
        self.assertEqual(self.client.get(url).status_code, 403)
