import csv
import io

from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from openpyxl import load_workbook

from apps.accounts.models import CourseTeacher
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.assessment.models import ExperimentSession
from apps.audit.models import AuditLog
from apps.experiments.models import PublishPlan


DOWNLOADS = {
    '/api/v1/teacher/import-templates/questions.csv': ('get', 'text/csv'),
    '/api/v1/teacher/import-templates/questions.xlsx': ('get', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    '/api/v1/teacher/import-templates/questions.docx': ('get', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    '/api/v1/teacher/import-templates/kcs.csv': ('get', 'text/csv'),
    '/api/v1/teacher/import-templates/course-kcs.csv': ('get', 'text/csv'),
    '/api/v1/teacher/accounts/students/import-template.xlsx': ('get', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    '/api/v1/teacher/experiments/{experiment_id}/kt.csv': ('get', 'text/csv'),
    '/api/v1/teacher/experiments/{experiment_id}/navigation.csv': ('get', 'text/csv'),
    '/api/v1/teacher/experiments/{experiment_id}/identity.csv': ('get', 'text/csv'),
    '/api/v1/teacher/experiments/{experiment_id}/identified.xlsx': ('post', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    '/api/v1/teacher/experiments/{experiment_id}/navigation-identified.csv': ('post', 'text/csv'),
    '/api/v1/teacher/courses/{course_id}/data/export-jobs/{job_id}/download/': ('get', 'application/zip'),
    '/api/v1/teacher/courses/{course_id}/experiments/{experiment_id}/sessions/{session_id}/word/': ('post', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    '/api/v1/teacher/publish-plans/{plan_id}/preview/export/': ('get', 'text/csv'),
}


class TeacherDownloadSchemaTests(SimpleTestCase):
    def test_all_downloads_expose_binary_success_and_session_error_contracts(self):
        schema = SchemaGenerator().get_schema(public=True)
        for path, (method, mime) in DOWNLOADS.items():
            with self.subTest(path=path):
                self.assertTrue(path in schema['paths'], path)
                operation = schema['paths'][path][method]
                self.assertIn(mime, operation['responses']['200']['content'])
                self.assertEqual(operation['responses']['200']['content'][mime]['schema'], {'type': 'string', 'format': 'binary'})
                for status in ('401', '403', '404'):
                    self.assertIn(status, operation['responses'])
                self.assertIn({'SessionAuth': []}, operation['security'])
                if method == 'post':
                    self.assertIn('X-CSRFToken', [row['name'] for row in operation['parameters']])
        fields = schema['paths']['/api/v1/teacher/experiments/{experiment_id}/identified.xlsx']['post']['requestBody']
        self.assertIn('application/x-www-form-urlencoded', fields['content'])

    def test_study_export_exposes_queued_json_receipt_and_csrf_errors(self):
        schema = SchemaGenerator().get_schema(public=True)
        operation = schema['paths']['/api/v1/teacher/research-studies/{study_id}/export-jobs/']['post']
        receipt = operation['responses']['202']['content']['application/json']['schema']
        self.assertEqual(receipt['required'], ['data', 'meta'])
        self.assertIn({'SessionAuth': []}, operation['security'])
        self.assertIn('X-CSRFToken', [row['name'] for row in operation['parameters']])
        self.assertIn('application/json', operation['requestBody']['content'])
        for status in ('400', '401', '403', '404'):
            self.assertEqual(operation['responses'][status]['content']['application/json']['schema']['required'], ['error'])


class TeacherDownloadApiTests(StudentApiFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.start().status_code, 201)
        self.session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.complete_session(self.session)
        self.client.force_login(self.teacher)

    def body(self, response):
        return b''.join(response.streaming_content) if response.streaming else response.content

    def test_question_and_student_templates_are_binary_and_not_json_enveloped(self):
        for path in list(DOWNLOADS)[:6]:
            with self.subTest(path=path):
                response = self.client.get(path)
                body = self.body(response)
                self.assertEqual(response.status_code, 200, body[:200])
                self.assertIn('attachment', response['Content-Disposition'])
                self.assertGreater(len(body), 30)
                self.assertFalse(body.startswith(b'{"data"'))

    def test_identified_export_preserves_form_fields_order_and_audit(self):
        response = self.client.post(reverse('teacher-experiment-identified-export', args=[self.experiment.pk]),
                                    {'fields': ['final_answer', 'score']})
        self.assertEqual(response.status_code, 200, response.content)
        sheet = load_workbook(io.BytesIO(response.content), read_only=True).active
        self.assertGreater(len(list(sheet.values)), 1)
        self.assertTrue(AuditLog.objects.filter(action='experiment.identified_export').exists())
        invalid = self.client.post(reverse('teacher-experiment-identified-export', args=[self.experiment.pk]), {'fields': ['unknown']})
        self.assertEqual(invalid.status_code, 400)
        self.assertIn('error', invalid.json())

    def test_word_retains_owner_only_scope_and_post_csrf(self):
        url = reverse('teacher-experiment-session-word', args=[self.course.pk, self.experiment.pk, self.session.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.content[:2], b'PK')
        self.assertTrue(AuditLog.objects.filter(action='experiment.session_word_report').exists())
        self.assertEqual(self.client.get(url).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.teacher)
        self.assertEqual(strict.post(url).status_code, 403)
        CourseTeacher.objects.create(course=self.course, teacher=self.outsider)
        self.outsider.role = 'teacher'
        self.outsider.save(update_fields=['role'])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_preview_csv_retains_six_columns_and_fixed_question_order(self):
        plan = PublishPlan.objects.get(experiment=self.experiment)
        response = self.client.get(reverse('teacher-publish-preview-export', args=[plan.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0], ['班级', '学号', '姓名', '题数', '题目顺序', '校验'])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][4], self.question.code)

    def test_templates_require_teacher_and_anonymous_is_401(self):
        path = '/api/v1/teacher/import-templates/questions.xlsx'
        self.client.logout()
        self.assertEqual(self.client.get(path).status_code, 401)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(path).status_code, 403)

    def test_private_job_download_rechecks_scope_expiry_and_snapshot_status(self):
        from datetime import timedelta
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from django.utils import timezone
        from .models import ExportJob, ExportSnapshot

        snapshot = ExportSnapshot.objects.create(course=self.course, experiment=self.experiment, created_by=self.teacher)
        with TemporaryDirectory() as directory:
            artifact = Path(directory) / "private.zip"
            artifact.write_bytes(b"private fixture archive")
            job = ExportJob.objects.create(course=self.course, experiment=self.experiment,
                snapshot=snapshot, created_by=self.teacher, status=ExportJob.Status.SUCCEEDED,
                artifact_path=str(artifact))
            url = reverse('teacher-export-job-download', args=[self.course.pk, job.pk])
            success = self.client.get(url)
            self.assertEqual(success.status_code, 200)
            self.assertEqual(self.body(success), artifact.read_bytes())
            self.assertEqual(AuditLog.objects.filter(action='analytics.identified_export_job_download').count(), 1)
            self.outsider.role = 'teacher'
            self.outsider.save(update_fields=['role'])
            self.client.force_login(self.outsider)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.teacher)
            for field in ('invalidated_at', 'redacted_at'):
                ExportSnapshot.objects.filter(pk=snapshot.pk).update(**{field: timezone.now()})
                self.assertEqual(self.client.get(url).status_code, 404)
                ExportSnapshot.objects.filter(pk=snapshot.pk).update(**{field: None})
            ExportJob.objects.filter(pk=job.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
            self.assertEqual(self.client.get(url).status_code, 404)
            ExportJob.objects.filter(pk=job.pk).update(expires_at=None, status=ExportJob.Status.QUEUED)
            self.assertEqual(self.client.get(url).status_code, 404)
            ExportJob.objects.filter(pk=job.pk).update(status=ExportJob.Status.SUCCEEDED)
            artifact.unlink()
            missing = self.client.get(url)
            self.assertEqual(missing.status_code, 404)
            self.assertIn('error', missing.json())
            self.assertNotIn(directory, missing.content.decode())
            self.assertEqual(AuditLog.objects.filter(action='analytics.identified_export_job_download').count(), 1)

    def test_longitudinal_export_preserves_study_owner_binding_and_audit(self):
        from apps.accounts.models import ResearchStudy, ResearchStudyCourse
        from .models import ExportJob
        study = ResearchStudy.objects.create(owner=self.teacher, name="Isolated research fixture")
        ResearchStudyCourse.objects.create(study=study, course=self.course)
        url = f"/api/v1/teacher/research-studies/{study.pk}/export-jobs/"
        response = self.client.post(url, {"package": "longitudinal"})
        self.assertEqual(response.status_code, 202, response.content[:200])
        job = ExportJob.objects.get(research_study=study)
        self.assertEqual(job.package, "longitudinal")
        self.assertEqual(job.status, ExportJob.Status.QUEUED)
        self.assertEqual(response.json()['data']['job_id'], str(job.pk))
        self.assertTrue(AuditLog.objects.filter(action='analytics.identified_export_job_create', details__research_study_id=study.pk).exists())
        self.outsider.role='teacher'
        self.outsider.save(update_fields=['role'])
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url, {"package": "longitudinal"}).status_code, 404)
        self.assertEqual(ExportJob.objects.filter(research_study=study).count(), 1)

    def test_study_export_rejects_missing_scope_and_csrf_and_allows_admin(self):
        from apps.accounts.models import ResearchStudy, ResearchStudyCourse, User
        from .models import ExportJob
        study = ResearchStudy.objects.create(owner=self.teacher, name="Empty study scope")
        url = reverse('teacher-research-study-export-create', args=[study.pk])
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertFalse(ExportJob.objects.filter(research_study=study).exists())
        ResearchStudyCourse.objects.create(study=study, course=self.course)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.teacher)
        self.assertEqual(strict.post(url).status_code, 403)
        self.assertFalse(ExportJob.objects.filter(research_study=study).exists())
        admin = User.objects.create_user(username='download-admin', role=User.Role.ADMIN)
        self.client.force_login(admin)
        response = self.client.post(url, {'package': 'longitudinal'})
        self.assertEqual(response.status_code, 202, response.content)
        job = ExportJob.objects.get(research_study=study)
        self.assertEqual(job.created_by, admin)
        self.assertEqual(job.status, ExportJob.Status.QUEUED)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 401)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(url).status_code, 403)
