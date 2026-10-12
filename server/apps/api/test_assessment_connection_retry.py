import json
import os
from unittest.mock import patch

from django.db import OperationalError, connection, transaction
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings

from .middleware import RequestIdMiddleware


@override_settings(ASSESSMENT_CONNECT_RETRY_ENABLED=True, SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=False)
class AssessmentConnectionRetryTests(SimpleTestCase):
    def request(self, path='/api/v1/student/attempts/1/draft/'):
        return RequestFactory().post(path, HTTP_X_KT_OPERATION_ID='a' * 32)

    def view(self, request):
        connection.get_new_connection({'password': 'PRIVATE PASSWORD'})
        return HttpResponse('ok')

    def test_retries_only_one_failed_connection_establishment_and_omits_credentials(self):
        original = connection.get_new_connection
        with patch.object(connection, 'get_new_connection', side_effect=[OperationalError('PRIVATE ERROR'), object()]) as connect, \
                self.assertLogs('kt.assessment_connection', level='WARNING') as logs:
            response = RequestIdMiddleware(self.view)(self.request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(connection.get_new_connection, original)
        row = json.loads(logs.records[0].getMessage())
        self.assertEqual(row['retry_limit'], 1)
        self.assertEqual(row['operation_id'], 'a' * 32)
        self.assertEqual(row['path'], '/api/v1/student/attempts/:id/draft/')
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_persistent_connection_failure_is_not_retried_indefinitely(self):
        with patch.object(connection, 'get_new_connection', side_effect=OperationalError('unavailable')) as connect, \
                self.assertLogs('kt.assessment_connection', level='WARNING'):
            with self.assertRaises(OperationalError):
                RequestIdMiddleware(self.view)(self.request())
        self.assertEqual(connect.call_count, 2)

    def test_backend_driver_connection_failure_is_retried(self):
        with patch.object(connection, 'get_new_connection', side_effect=[connection.Database.OperationalError('transport'), object()]) as connect, \
                self.assertLogs('kt.assessment_connection', level='WARNING'):
            self.assertEqual(RequestIdMiddleware(self.view)(self.request()).status_code, 200)
        self.assertEqual(connect.call_count, 2)

    def test_an_executed_business_operation_is_never_replayed(self):
        executions = []
        def view(request):
            connection.get_new_connection({})
            executions.append('business-write')
            raise OperationalError('result uncertain')
        with patch.object(connection, 'get_new_connection', return_value=object()) as connect:
            with self.assertRaises(OperationalError):
                RequestIdMiddleware(view)(self.request())
        self.assertEqual(executions, ['business-write'])
        self.assertEqual(connect.call_count, 1)

    def test_authentication_error_is_not_retried(self):
        error = OperationalError('credentials rejected')
        error.sqlstate = '28P01'
        with patch.object(connection, 'get_new_connection', side_effect=error) as connect:
            with self.assertRaises(OperationalError):
                RequestIdMiddleware(self.view)(self.request())
        self.assertEqual(connect.call_count, 1)

    @override_settings(ASSESSMENT_CONNECT_RETRY_ENABLED=False)
    def test_disabled_flag_keeps_original_connection_failure(self):
        with patch.object(connection, 'get_new_connection', side_effect=OperationalError('unavailable')) as connect:
            with self.assertRaises(OperationalError):
                RequestIdMiddleware(self.view)(self.request())
        self.assertEqual(connect.call_count, 1)

    def test_unrelated_paths_keep_original_connection_failure(self):
        with patch.object(connection, 'get_new_connection', side_effect=OperationalError('unavailable')) as connect:
            with self.assertRaises(OperationalError):
                RequestIdMiddleware(self.view)(self.request('/api/v1/teacher/courses/'))
        self.assertEqual(connect.call_count, 1)


@override_settings(ASSESSMENT_CONNECT_RETRY_ENABLED=True, SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=True)
class AssessmentConnectionRetryPostgresTests(TransactionTestCase):
    def test_diagnostics_preserves_both_connection_attempts_and_a_single_database_write(self):
        from apps.accounts.models import User
        connection.close()
        original_connect = connection.get_new_connection
        calls = 0
        def initial_failure(parameters):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise connection.Database.OperationalError('PRIVATE TRANSPORT ERROR')
            return original_connect(parameters)
        def view(request):
            with transaction.atomic():
                User.objects.create(username='connect-retry-single-write')
            return HttpResponse('ok')
        request = RequestFactory().post('/api/v1/student/attempts/1/draft/')
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), \
                patch.object(connection, 'get_new_connection', side_effect=initial_failure), \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs, \
                self.assertLogs('kt.assessment_connection', level='WARNING'):
            self.assertEqual(RequestIdMiddleware(view)(request).status_code, 200)
        self.assertEqual(calls, 2)
        self.assertEqual(User.objects.filter(username='connect-retry-single-write').count(), 1)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual([item['successful'] for item in row['connections']], [False, True])
        self.assertNotIn('PRIVATE', json.dumps(row))
