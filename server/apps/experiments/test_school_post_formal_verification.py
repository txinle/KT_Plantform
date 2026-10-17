from apps.api.test_support import domain_payload
import importlib.util
import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import resolve


def module():
    path = Path(settings.BASE_DIR) / 'scripts/ops/school_post_formal_verification.py'
    if not path.exists():
        raise AssertionError('Independent post-formal verification script missing')
    spec = importlib.util.spec_from_file_location('school_post_formal', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SchoolPostFormalContractTests(SimpleTestCase):
    def test_formal_gate_rejects_short_failed_and_changed_source(self):
        helper = module()
        binding = {'candidate_archive_sha256': 'a' * 64, 'frontend_archive_sha256': 'b' * 64}
        config = {'run_id': helper.RUN, 'source_sha256': 'a' * 64, 'frontend_sha256': 'b' * 64}
        status = {'run_id': helper.RUN, 'state': 'passed'}
        helper.require_formal(status, {'passed': True, 'checks': {'actual': True}}, config, binding)
        for changed in ({**status, 'state': 'short-completed'}, {**status, 'run_id': 'other'}):
            with self.assertRaises(ValueError):
                helper.require_formal(changed, {'passed': True, 'checks': {'actual': True}}, config, binding)
        with self.assertRaises(ValueError):
            helper.require_formal(status, {'passed': True, 'checks': {'actual': False}}, config, binding)
        with self.assertRaises(ValueError):
            helper.require_formal(status, {'passed': True, 'checks': {'actual': True}}, {**config, 'source_sha256': 'c' * 64}, binding)

    def test_exact_existing_routes_and_download_reject_offsite(self):
        helper = module()
        fixture = {'course_id': 2, 'experiment_id': 3, 'task_id': 4, 'question_id': 5, 'class_id': 6}
        paths = helper.read_paths(fixture)
        for rows in paths.values():
            for _, path in rows:
                resolve(path.split('?')[0])
        path = '/analytics/courses/2/export-jobs/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/download/'
        self.assertEqual(helper.require_download(path, fixture), path)
        for bad in ('https://example.org' + path, '/media/exports/file.zip', path.replace('/2/', '/7/')):
            with self.assertRaises(ValueError):
                helper.require_download(bad, fixture)

    def test_safe_plan_and_reports_do_not_contain_queries_or_credentials(self):
        helper = module()
        secret = 'password-cookie-username-answer-SQL'
        plan = [{'Plan': {'Node Type': 'Nested Loop', 'Relation Name': secret,
                         'Plans': [{'Node Type': 'Index Scan', 'Index Cond': secret}]}}]
        self.assertEqual(helper.safe_plan(plan), {'node_types': ['Index Scan', 'Nested Loop']})
        response = Mock(status=200, elapsed_seconds=.2, response_bytes=30)
        row = helper.safe_measurement('teacher', 'preview', '/api/test/', response)
        self.assertEqual(set(row), {'role', 'operation', 'path', 'status', 'elapsed_seconds', 'response_bytes'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            helper.write_json(path, {'measurements': [row]}, 0o644)
            self.assertNotIn(secret, path.read_text())

    def test_private_browser_handoff_has_relative_existing_paths_no_passwords(self):
        import http.cookiejar
        import sys
        helper = module()
        fixture = {'course_id': 2, 'experiment_id': 3, 'task_id': 4, 'question_id': 5, 'class_id': 6,
                   'password': 'private-fixture-password', 'usernames': ['private-username']}
        verifier = helper.Verification()
        verifier.fixture = fixture
        verifier.bound = {'run_id': helper.RUN, 'source_sha256': 'a' * 64, 'frontend_sha256': 'b' * 64}
        clients = {role: helper.HTTPClient() for role in ('student', 'teacher')}
        for client in clients.values():
            client.cookies.set_cookie(http.cookiejar.Cookie(0, 'sessionid', 'private-cookie', None, False,
                '127.0.0.1', False, False, '/', True, False, None, True, None, None, {'HttpOnly': None}))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'pwd': Mock()}):
            private = Path(directory) / 'private'
            private.mkdir()
            public = Path(directory) / 'public'
            public.mkdir()
            session = Path(directory) / 'handoff.json'
            with patch.object(helper, 'PRIVATE', private), patch.object(helper, 'PUBLIC', public), \
                    patch.object(helper, 'SESSION_FILE', session), patch.object(helper.os, 'chown', create=True):
                verifier.save_browser_sessions(clients, helper.read_paths(fixture))
            body = json.loads(session.read_text())
            self.assertEqual(set(body['roles']), {'student', 'teacher'})
            for role in body['roles'].values():
                self.assertTrue(role['cookies'])
                for path in role['paths']:
                    self.assertTrue(path.startswith('/') and not path.startswith('//'))
                    resolve(path.split('?')[0])
            self.assertNotIn('private-fixture-password', session.read_text())
            self.assertNotIn('private-username', session.read_text())


class SchoolPostFormalCookieSQLTests(TransactionTestCase):
    def test_sql_replay_uses_real_login_cookie_and_explain_without_raw_sql(self):
        from apps.accounts.models import User
        from django.test import Client
        helper = module()
        User.objects.create_user(username='sql-proof-fixture', password='local-proof-only', role='student')
        client = Client(enforce_csrf_checks=True)
        response = client.get('/api/v1/auth/csrf/')
        token = domain_payload(response)['csrf_token']
        response = client.post('/api/v1/auth/login/', data=json.dumps({'username': 'sql-proof-fixture', 'password': 'local-proof-only'}),
                               content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        payload = {'expected_database': None, 'requests': [{'role': 'student', 'operation': 'session',
                    'path': '/api/v1/auth/session/', 'cookies': {name: morsel.value for name, morsel in client.cookies.items()}}]}
        report = helper.sql_probe(payload)
        self.assertTrue(report['checks']['authenticated_replays'])
        self.assertTrue(report['checks']['select_plan_observed'])
        self.assertTrue(report['checks']['transactions_rolled_back'])
        self.assertGreater(report['measurements'][0]['query_count'], 0)
        for secret in ('sql-proof-fixture', 'local-proof-only', client.cookies['sessionid'].value, 'SELECT ', 'query_text'):
            self.assertNotIn(secret, json.dumps(report))
        payload['requests'][0]['cookies'] = {}
        denied = helper.sql_probe(payload)
        self.assertFalse(denied['checks']['authenticated_replays'])


from apps.assessment.test_student_api import StudentApiFixtureMixin


@override_settings(STUDENT_SHARED_RESULTS_ENABLED=True,
                   STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}),
                   CLASS_CENTER_ENABLED=True, STUDENT_LEARNING_ANALYTICS_ENABLED=True)
class SchoolPostFormalOwnResultsSQLTests(StudentApiFixtureMixin, TransactionTestCase):
    prepared_paper_fixture = True

    @override_settings(ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True, ASSESSMENT_PREPARED_ADMISSION_MODE="postgres_transaction")
    def test_real_cookie_full_snapshot_results_and_question_rollback(self):
        from django.test import Client
        from apps.assessment.models import ExperimentSession
        from apps.analytics.models import StudentExperimentResultSnapshot
        helper = module()
        client = Client(enforce_csrf_checks=True)
        token = domain_payload(client.get('/api/v1/auth/csrf/'))['csrf_token']
        self.assertEqual(client.post('/api/v1/auth/login/', data=json.dumps({
            'username': self.student.username, 'password': 'test-password'}), content_type='application/json',
            HTTP_X_CSRFTOKEN=token).status_code, 200)
        token = client.cookies['csrftoken'].value
        self.stream = uuid.uuid4()
        started = client.post(f'/api/v1/student/tasks/{self.task.pk}/start/', data=json.dumps({'client_instance_id': str(self.stream), 'navigation_id': str(uuid.uuid4())}),
                              content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(started.status_code, 201)
        session = ExperimentSession.objects.get(pk=domain_payload(started)['session_id'])
        self.session = session
        attempt = session.attempts.get()
        from apps.assessment.test_capture_v2 import CaptureV2Tests
        from django.urls import reverse
        from django.utils import timezone
        from apps.assessment.session_submission_services import claim_session_submission, run_session_submission
        event = CaptureV2Tests.transition(self, attempt, None, 'B')
        captured = client.post(reverse('assessment_process:capture_v2_batch'), data=json.dumps({
            'client_instance_id': str(self.stream), 'events': [event]}), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(captured.status_code, 200, captured.content)
        lease = domain_payload(started)['runtime']['initial_writer_lease']
        saved = client.post(reverse('assessment_api:save_draft', args=[attempt.pk]), data=json.dumps({
            'answer': 'B', 'target_event_id': event['event_id'], 'expected_version': 0,
            'writer_lease_token': lease['writer_lease_token'], 'writer_client_instance_id': str(self.stream)}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(saved.status_code, 200, saved.content)
        attempt.refresh_from_db()
        accepted = client.post(reverse('assessment_api:finish', args=[session.pk]), data=json.dumps({
            'submission_token': str(uuid.uuid4()), 'client_clicked_at': timezone.now().isoformat(),
            'expected_versions': [{'attempt_id': attempt.pk, 'response_version': attempt.response_version,
                'target_event_id': str(attempt.active_response_event_id)}]}), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 202, accepted.content)
        claimed = claim_session_submission(worker_id='post-formal-sql-test')
        self.assertEqual(str(claimed.pk), domain_payload(accepted)['submission_id'])
        self.assertEqual(run_session_submission(claimed).status, 'completed')
        detail = f'/api/v1/student/courses/{self.course.pk}/learning/experiments/{self.experiment.pk}/'
        paths = [detail, f'/api/v1/student/tasks/{self.task.pk}/results/',
                 detail + f'questions/{session.attempts.first().assignment.snapshot_id}/']
        payload = {'expected_database': None, 'requests': [{'role': 'student', 'operation': 'own_result', 'path': path,
                    'cookies': {name: morsel.value for name, morsel in client.cookies.items()}} for path in paths]}
        before = StudentExperimentResultSnapshot.objects.count()
        report = helper.sql_probe(payload)
        self.assertTrue(report['passed'], report)
        self.assertEqual([row['status'] for row in report['measurements']], [200, 200, 200])
        self.assertEqual(StudentExperimentResultSnapshot.objects.count(), before)
