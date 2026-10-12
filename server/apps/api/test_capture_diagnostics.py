import json
import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection, transaction
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings

from .middleware import RequestIdMiddleware


class CaptureDiagnosticGuardTests(SimpleTestCase):
    @override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=False)
    def test_disabled_diagnostics_does_not_touch_database_or_accept_client_id(self):
        request = RequestFactory().post('/api/v1/student/attempts/1/draft/', HTTP_X_REQUEST_ID='a' * 32)
        response = RequestIdMiddleware(lambda request: HttpResponse('ok'))(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(request.request_id, 'a' * 32)

    @override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=True)
    def test_diagnostic_flag_fails_closed_for_production_database(self):
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_platform'}):
            with self.assertRaises(ImproperlyConfigured):
                RequestIdMiddleware(lambda request: HttpResponse('ok'))


@override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=True)
class CaptureDiagnosticSQLTests(TestCase):
    def test_request_and_query_times_are_correlated_without_parameters(self):
        def view(request):
            with connection.cursor() as cursor:
                cursor.execute('SELECT %s::text', ['PRIVATE ANSWER'])
                self.assertEqual(cursor.fetchone()[0], 'PRIVATE ANSWER')
            return HttpResponse('ok')

        request = RequestFactory().post('/api/v1/student/attempts/123/draft/?password=SECRET',
            HTTP_X_REQUEST_ID='a' * 32, data={'answer': 'PRIVATE ANSWER'})
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            response = RequestIdMiddleware(view)(request)
        self.assertEqual(response.status_code, 200)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(row['request_id'], 'a' * 32)
        self.assertEqual(row['path'], '/api/v1/student/attempts/:id/draft/')
        self.assertEqual(row['phase'], 'middleware_request_complete')
        self.assertIn('middleware_started_at', row)
        self.assertIn('middleware_ended_at', row)
        self.assertEqual(len(row['queries']), 1)
        self.assertGreaterEqual(row['queries'][0]['seconds'], 0)
        self.assertIn('ended_at', row['queries'][0])
        self.assertIn('clock_discontinuity', row['queries'][0])
        self.assertRegex(row['queries'][0]['fingerprint'], '^[a-f0-9]{16}$')
        if connection.vendor == 'postgresql':
            self.assertGreater(row['queries'][0]['backend_pid'], 0)
        for private in ('PRIVATE ANSWER', 'SECRET', 'SELECT', '/123/'):
            self.assertNotIn(private, json.dumps(row))

    def test_exception_still_emits_timing_and_does_not_change_failure(self):
        def view(request):
            raise RuntimeError('PRIVATE MESSAGE')

        request = RequestFactory().post('/api/v1/student/sessions/123/finish/')
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            with self.assertRaisesRegex(RuntimeError, 'PRIVATE MESSAGE'):
                RequestIdMiddleware(view)(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(row['error_type'], 'RuntimeError')
        self.assertNotIn('PRIVATE', json.dumps(row))


@override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=True)
class CaptureDiagnosticCommitTests(TransactionTestCase):
    def test_connection_establishment_and_its_failure_are_timed_without_credentials(self):
        connection.close()
        def view(request):
            connection.ensure_connection()
            return HttpResponse('ok')

        request = RequestFactory().post('/api/v1/student/attempts/1/submit/')
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            RequestIdMiddleware(view)(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(len(row['connections']), 1)
        self.assertTrue(row['connections'][0]['successful'])
        self.assertGreaterEqual(row['connections'][0]['seconds'], 0)
        self.assertEqual(row['worker_pid'], os.getpid())
        connection.close()
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), \
                patch.object(connection, 'get_new_connection', side_effect=OperationalError('PRIVATE PASSWORD')), \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            with self.assertRaises(OperationalError):
                RequestIdMiddleware(view)(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertFalse(row['connections'][0]['successful'])
        self.assertEqual(row['connections'][0]['error_type'], 'OperationalError')
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_savepoints_rollbacks_autocommit_and_callbacks_are_distinct(self):
        def view(request):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            with transaction.atomic():
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 2')
                transaction.on_commit(lambda: connection.cursor().execute('SELECT 3'))
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 4')
                    raise ValueError('rollback')
            except ValueError:
                pass
            return HttpResponse('ok')

        request = RequestFactory().post('/api/v1/student/attempts/1/submit/', HTTP_X_KT_OPERATION_ID='b' * 32)
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            RequestIdMiddleware(view)(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(row['operation_id'], 'b' * 32)
        self.assertEqual(len(row['commits']), 1)
        self.assertEqual(len(row['rollbacks']), 1)
        self.assertEqual(row['transactions']['savepoints'], 1)
        self.assertEqual(row['transactions']['savepoint_releases'], 1)
        self.assertGreaterEqual(row['transactions']['autocommit_statements'], 2)
        self.assertGreaterEqual(row['commit_hooks_seconds'], 0)
        self.assertEqual(len(row['commit_hooks']), 1)
        self.assertIn('ended_at', row['commits'][0])
        self.assertIn('ended_at', row['commit_hooks'][0])
        self.assertEqual(row['implicit_commit_timing'], 'included_in_autocommit_statement')

    def test_commit_time_is_separate_and_connection_commit_is_restored(self):
        original_commit = connection.commit

        def view(request):
            for _ in range(2):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 1')
            return HttpResponse('ok')

        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            RequestIdMiddleware(view)(RequestFactory().post('/api/v1/student/attempts/123/draft/'))
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(len(row['commits']), 2)
        self.assertTrue(all(commit['seconds'] >= 0 for commit in row['commits']))
        self.assertEqual(connection.commit, original_commit)
