import json
import os
from unittest.mock import Mock, patch
from contextlib import contextmanager

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection, transaction
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings

from .middleware import RequestIdMiddleware


class CaptureDiagnosticGuardTests(SimpleTestCase):
    @override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=False, ASSESSMENT_REQUEST_TIMING_ENABLED=True)
    def test_production_timing_records_slow_requests_without_sql_text_or_answers(self):
        fake = Mock(vendor='postgresql', in_atomic_block=True)
        fake.get_autocommit.return_value = False
        callbacks = []
        @contextmanager
        def wrapper(callback):
            callbacks.append(callback)
            yield
        fake.execute_wrapper.side_effect = wrapper
        execute = Mock()
        def view(request):
            from config.capture_timing import request_phase

            self.assertEqual(len(callbacks), 1)
            with request_phase(request, 'session_start'):
                callbacks[0](execute, 'SELECT secret_answer FROM assessment_attempt WHERE id = %s',
                             ['PRIVATE'], False, {})
            fake.commit()
            return HttpResponse('PRIVATE ANSWER')
        request = RequestFactory().post('/api/v1/student/tasks/35/start/?secret=PRIVATE')
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_platform', 'SCHOOL_CAPTURE_DIAGNOSTICS_MODE': 'sql'}), \
                patch('apps.api.middleware.connection', fake), \
                patch('apps.api.middleware.SLOW_REQUEST_SECONDS', 0, create=True), \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            response = RequestIdMiddleware(view)(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(row['diagnostics_mode'], 'light')
        self.assertEqual(row['sql_count'], 1)
        self.assertEqual(row['commit_count'], 1)
        self.assertEqual(row['queries'], [])
        self.assertGreaterEqual(row['session_start_seconds'], 0)
        self.assertEqual(row['slowest_sql']['relations'], ['assessment_attempt'])
        self.assertEqual(execute.call_count, 1)
        self.assertNotIn('PRIVATE', json.dumps(row))
        self.assertNotIn('secret_answer', json.dumps(row))

    @override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=False, ASSESSMENT_REQUEST_TIMING_ENABLED=True)
    def test_production_timing_does_not_log_fast_requests(self):
        request = RequestFactory().post('/api/v1/student/tasks/35/start/')
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_platform'}), \
                patch('apps.api.middleware.SLOW_REQUEST_SECONDS', 1000, create=True), \
                patch('apps.api.middleware.emit_diagnostic') as emit:
            response = RequestIdMiddleware(lambda request: HttpResponse('ok'))(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn('X-Capture-Seconds', response)
        emit.assert_not_called()

    @override_settings(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED=True)
    def test_full_response_bytes_include_the_existing_api_envelope(self):
        request = RequestFactory().get('/api/v1/student/attempts/1/')
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            response = RequestIdMiddleware(lambda request: JsonResponse({'value': 'PRIVATE'}))(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(row['response_body_bytes'], len(response.content))
        self.assertEqual(json.loads(response.content)['data'], {'value': 'PRIVATE'})
        self.assertNotIn('PRIVATE', json.dumps(row))

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
class CaptureDiagnosticLightTests(SimpleTestCase):
    def test_light_mode_aggregates_driver_intervals_without_sql_details_or_extra_sql(self):
        fake = Mock(vendor='postgresql', in_atomic_block=True)
        fake.get_autocommit.return_value = False
        active = []
        @contextmanager
        def wrapper(callback):
            active.append(callback)
            yield
        fake.execute_wrapper.side_effect = wrapper
        execute = Mock()
        cursor = Mock()
        cursor.fetchone.return_value = (123,)
        def view(request):
            fake.get_new_connection({'password': 'PRIVATE PASSWORD'})
            for sql in ('SELECT PRIVATE', 'SAVEPOINT private_savepoint', 'RELEASE SAVEPOINT private_savepoint'):
                active[0](execute, sql, ['PRIVATE ANSWER'], False, {'cursor': cursor})
            fake.commit()
            fake.run_and_clear_commit_hooks()
            return HttpResponse('ok')
        request = RequestFactory().post('/student/sessions/123/finish/', data={'answer': 'PRIVATE ANSWER'})
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e', 'SCHOOL_CAPTURE_DIAGNOSTICS_MODE': 'light'}), \
                patch('apps.api.middleware.connection', fake), \
                patch('apps.api.middleware.hashlib.md5') as fingerprint, \
                self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            fingerprint.return_value.hexdigest.return_value = 'c' * 32
            response = RequestIdMiddleware(view)(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(execute.call_count, 3)
        fingerprint.assert_not_called()
        self.assertEqual(row['diagnostics_mode'], 'light')
        self.assertEqual(row['sql_count'], 3)
        self.assertEqual(row['connection_count'], 1)
        self.assertEqual(row['commit_count'], 1)
        self.assertEqual(row['transactions']['savepoints'], 1)
        for key in ('queries', 'connections', 'commits', 'commit_hooks'):
            self.assertEqual(row[key], [])
        for key in ('sql_seconds', 'connection_seconds', 'commit_seconds', 'application_excluding_measured_database_seconds'):
            self.assertGreaterEqual(row[key], 0)
        self.assertEqual(row['response_body_bytes'], 2)
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_invalid_mode_is_rejected_before_a_request(self):
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e', 'SCHOOL_CAPTURE_DIAGNOSTICS_MODE': 'unknown'}):
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
    def test_session_response_save_is_inside_full_diagnostic_and_nested_timings(self):
        from apps.accounts.session_middleware import SessionMiddleware
        from django.contrib.sessions.backends.db import SessionStore
        existing = SessionStore()
        existing['private'] = 'PRIVATE ANSWER'
        existing.save()
        def view(request):
            self.assertEqual(request.session['private'], 'PRIVATE ANSWER')
            request.session['private'] = 'PRIVATE ANSWER'
            return HttpResponse('ok')
        request = RequestFactory().post('/api/v1/student/attempts/1/draft/')
        request.COOKIES['sessionid'] = existing.session_key
        with patch.dict(os.environ, {'POSTGRES_DB': 'kt_e2e'}), self.assertLogs('kt.capture_diagnostics', level='WARNING') as logs:
            response = RequestIdMiddleware(SessionMiddleware(view))(request)
        row = json.loads(logs.records[-1].getMessage())
        self.assertEqual(response.status_code, 200)
        self.assertIn('timing_nesting', row)
        self.assertIn('session', row['timing_nesting'])
        self.assertEqual(row['session_save_count'], 1)
        self.assertEqual(row['session_load_count'], 1)
        self.assertGreaterEqual(row['session_load_seconds'], 0)
        self.assertGreaterEqual(row['session_save_seconds'], 0)
        self.assertGreaterEqual(row['session_response_seconds'], row['session_save_seconds'])
        self.assertGreater(row['sql_count'], 0)
        self.assertGreater(row['commit_count'], 0)
        self.assertGreaterEqual(row['measured_database_union_seconds'], 0)
        state = request.META['kt.capture_timing']
        self.assertGreaterEqual(state['capture_metrics']['middleware_log_serialize_seconds'], 0)
        self.assertGreaterEqual(state['capture_metrics']['middleware_log_output_seconds'], 0)
        self.assertNotIn('PRIVATE ANSWER', json.dumps(row))

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
