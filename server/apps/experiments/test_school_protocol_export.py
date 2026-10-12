"""Real export job, package, and raw-fact reconciliation for school-only verification."""
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, TransactionTestCase, override_settings

from apps.analytics.models import ExportJob, ResearchSessionResponse
from apps.analytics.services import run_export_job
from . import test_school_architecture_results as fixtures


def helper(test):
    path = Path(__file__).resolve().parents[2] / 'scripts/ops/school_protocol_export.py'
    test.assertTrue(path.is_file(), 'Bounded production-job export helper is missing')
    return importlib.import_module('scripts.ops.school_protocol_export')


class SchoolProtocolExportBoundaryTests(SimpleTestCase):
    def test_only_fixed_local_http_origin_is_allowed(self):
        module = helper(self)
        self.assertEqual(module.require_origin('http://127.0.0.1:18080'), 'http://127.0.0.1:18080')
        for origin in ('https://example.org', 'http://localhost:18080', 'http://127.0.0.1:18080/other'):
            with self.assertRaises(module.ExportVerificationError):
                module.require_origin(origin)

    def test_complete_source_claim_binding_is_required(self):
        module = helper(self)
        source = {'source_sha256': 'a' * 64, 'frontend_sha256': 'b' * 64, 'config_sha256': 'c' * 64,
                  'shell_url': '/static/spa-assets/capture-shell-' + 'd' * 20 + '.html'}
        fixture = {'database': 'kt_e2e', 'capture_protocol': 'school-capture-v2-protocol-v1',
                   'run_id': 'arch-smoke', 'student_count': 3, 'item_count': 20,
                   'source_config': source, 'task_id': 7, 'usernames': ['a', 'b', 'c']}
        claim = {'task_id': 7, 'run_id': fixture['run_id'], 'manifest_sha256': 'e' * 64}
        self.assertIsNone(module.require_manifest_binding(fixture, claim, source, 'e' * 64))
        claim['manifest_sha256'] = 'f' * 64
        with self.assertRaises(module.ExportVerificationError):
            module.require_manifest_binding(fixture, claim, source, 'e' * 64)

    def test_budget_is_hard_bounded_and_missing_data_never_passes(self):
        module = helper(self)
        with self.assertRaises(module.ExportVerificationError):
            module.Budget(301)
        with self.assertRaises(module.ExportVerificationError):
            module.Budget(0)


class SchoolProtocolExportJobTests(fixtures.StudentApiFixtureMixin, TransactionTestCase):
    materialize = fixtures.SchoolArchitectureResultsTests.materialize

    def setUp(self):
        from apps.assessment.models import Attempt, ExperimentSession, SubmissionEvent
        from apps.assessment.response_snapshot_services import create_response_snapshot
        super().setUp()
        self.assertEqual(self.start().status_code, 201)
        self.session = ExperimentSession.objects.get(student=self.student, class_task=self.task)
        self.complete_session(self.session)
        self.attempt = self.session.attempts.select_related('assignment__snapshot').get()
        snapshot = create_response_snapshot(attempt=self.attempt, content='B')
        Attempt.objects.filter(pk=self.attempt.pk).update(final_response_snapshot=snapshot, score=1, is_correct=True)
        SubmissionEvent.objects.filter(attempt=self.attempt).update(final_response_snapshot=snapshot, score=1, is_correct=True)
        self.materialize()

    def create_download(self, module, directory):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        client.get('/api/v1/auth/csrf/')
        prefix = f'/api/v1/teacher/courses/{self.course.pk}/data/export-jobs/'
        response = client.post(prefix, data=json.dumps({'package': 'experiment', 'confirm_identified': True,
            'scope': {'scope_version': 2, 'experiment_id': self.experiment.pk,
                      'class_mode': 'single', 'class_id': self.class_group.pk}}),
            content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 201, response.content)
        job_id = response.json()['data']['job_id']
        job = ExportJob.objects.get(pk=job_id)
        run_export_job(job)
        response = client.get(prefix + job_id + '/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['status'], 'succeeded')
        fetched = client.get(response.json()['data']['download_url'])
        self.assertEqual(fetched.status_code, 200)
        path = Path(directory) / 'download.zip'
        path.write_bytes(b''.join(fetched.streaming_content))
        fetched.close()
        return job, path

    def test_real_job_download_crc_counts_and_authoritative_hashes(self):
        module = helper(self)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            job, path = self.create_download(module, directory)
            result = module.verify_archive(job, path, [self.session], module.Budget(30))
        self.assertTrue(result['passed'])
        self.assertTrue(result['checks']['archive_crc_valid'])
        self.assertTrue(result['checks']['exact_package_entries'])
        self.assertTrue(result['checks']['authoritative_fact_hashes_match'])
        self.assertEqual(result['response_count'], 1)
        self.assertEqual(result['artifact_sha256'], job.artifact_sha256)
        rendered = json.dumps(result)
        for secret in (self.student.username, self.student.student_number, 'final_answer'):
            self.assertNotIn(secret, rendered)

    def test_tampered_materialized_fact_cannot_pass_real_package_verification(self):
        module = helper(self)
        row = ResearchSessionResponse.objects.get()
        altered = list(row.row)
        altered[-3] = 'not the authoritative answer'
        ResearchSessionResponse.objects.filter(pk=row.pk).update(row=altered)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            job, path = self.create_download(module, directory)
            with self.assertRaises(module.ExportVerificationError):
                module.verify_archive(job, path, [self.session], module.Budget(30))

    def test_wrong_download_hash_cannot_pass(self):
        module = helper(self)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            job, path = self.create_download(module, directory)
            path.write_bytes(path.read_bytes() + b'changed')
            with self.assertRaises(module.ExportVerificationError):
                module.verify_archive(job, path, [self.session], module.Budget(30))

    def test_job_scope_and_owner_are_verified_before_worker(self):
        module = helper(self)
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            job, _ = self.create_download(module, directory)
            fixture = {'course_id': self.course.pk, 'experiment_id': self.experiment.pk,
                       'class_id': self.class_group.pk}
            module.require_job(job, fixture, self.teacher)
            fixture['class_id'] += 1000
            with self.assertRaises(module.ExportVerificationError):
                module.require_job(job, fixture, self.teacher)
            fixture['class_id'] = self.class_group.pk
            with self.assertRaises(module.ExportVerificationError):
                module.require_job(job, fixture, self.student)
