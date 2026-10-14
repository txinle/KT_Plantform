import importlib.util
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import clear_url_caches, get_resolver
from types import SimpleNamespace
from unittest.mock import patch
import json


class GunicornStartupTests(SimpleTestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('tested_gunicorn', Path(settings.BASE_DIR) / 'config/gunicorn.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_capture_hook_records_complete_wsgi_boundary_with_no_headers_or_answers(self):
        module = self.module()
        request = SimpleNamespace(headers=[('X-REQUEST-ID', 'a' * 32), ('Cookie', 'SECRET')], method='POST', path='/student/attempts/123/draft/')
        with patch.dict('os.environ', {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}), \
                patch('config.capture_timing.time.time', side_effect=[100, 101]), \
                patch('config.capture_timing.time.perf_counter', side_effect=[10, 10.2, 10.2, 10.21, 10.24]), \
                patch.object(module.logger, 'warning') as logged:
            module.pre_request(None, request)
            module.post_request(SimpleNamespace(pid=321), request, {}, None)
        value = json.loads(logged.call_args.args[0])
        self.assertEqual(value['request_id'], 'a' * 32)
        self.assertEqual(value['worker_pid'], 321)
        self.assertEqual(value['path'], '/student/attempts/:id/draft/')
        self.assertEqual(value['started_at'], 100)
        self.assertAlmostEqual(value['seconds'], .2)
        self.assertAlmostEqual(request.capture_timing['capture_metrics']['gunicorn_log_serialize_seconds'], .01)
        self.assertAlmostEqual(request.capture_timing['capture_metrics']['gunicorn_log_output_seconds'], .03)
        self.assertIn('after gunicorn_end', value['own_log_timing_scope'])
        self.assertNotIn('SECRET', json.dumps(value))

    def test_complete_wsgi_hook_is_disabled_outside_isolated_capture_diagnostics(self):
        module = self.module()
        request = SimpleNamespace()
        with patch.dict('os.environ', {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '0', 'POSTGRES_DB': 'kt_platform'}), \
                patch.object(module.logger, 'warning') as logged:
            module.pre_request(None, request)
            module.post_request(None, request, {}, None)
        logged.assert_not_called()
        with patch.dict('os.environ', {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'production'}), self.assertRaises(ValueError):
            module.pre_request(None, request)

    def test_wsgi_and_middleware_boundaries_reach_post_request_and_context_is_reset(self):
        from config.capture_timing import TIMING_ENV_KEY, WSGICaptureTiming, pending_timing, mark

        module = self.module()
        request = SimpleNamespace(headers=[('X-Request-ID', 'a' * 32), ('X-KT-Operation-ID', 'b' * 32),
            ('X-Request-Started-At', '99.95')], method='POST', path='/student/attempts/123/draft/')
        environ = {'PATH_INFO': request.path, 'HTTP_X_REQUEST_STARTED_AT': '99.95'}
        def app(environ, start_response):
            state = environ[TIMING_ENV_KEY]
            mark(state, 'middleware_start')
            mark(state, 'middleware_end')
            start_response('200 OK', [])
            return [b'ok']

        with patch.dict('os.environ', {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}), \
                patch('config.capture_timing.time.time', side_effect=[100, 100.1, 100.12, 100.2, 100.21, 100.22, 100.3, 100.4]), \
                patch('config.capture_timing.time.perf_counter', side_effect=[10, 10.1, 10.12, 10.2, 10.21, 10.22, 10.3, 10.4, 10.4, 10.41, 10.43]), \
                patch.object(module.logger, 'warning') as logged:
            module.pre_request(None, request)
            self.assertEqual(list(WSGICaptureTiming(app)(environ, lambda status, headers, exc_info=None: None)), [b'ok'])
            module.post_request(SimpleNamespace(pid=321), request, environ, None)
            self.assertIsNone(pending_timing.get())
        row = json.loads(logged.call_args.args[0])
        self.assertEqual(row['operation_id'], 'b' * 32)
        self.assertAlmostEqual(row['gunicorn_pre_to_wsgi_seconds'], .1)
        self.assertAlmostEqual(row['wsgi_to_middleware_seconds'], .02)
        self.assertAlmostEqual(row['middleware_seconds'], .08)
        self.assertAlmostEqual(row['wsgi_application_seconds'], .12)
        self.assertAlmostEqual(row['wsgi_iteration_seconds'], .08)
        self.assertAlmostEqual(row['nginx_ingress_to_gunicorn_pre_wall_seconds'], .05)
        self.assertFalse(row['clock_discontinuity'])
        self.assertEqual(row['nginx_clock_alignment'], 'assumed_shared_host_clock')
        self.assertAlmostEqual(request.capture_timing['capture_metrics']['gunicorn_log_serialize_seconds'], .01)
        self.assertAlmostEqual(request.capture_timing['capture_metrics']['gunicorn_log_output_seconds'], .02)

    def test_invalid_request_id_still_clears_pending_timing(self):
        from config.capture_timing import pending_timing

        module = self.module()
        request = SimpleNamespace(headers=[('X-Request-ID', 'PRIVATE')])
        with patch.dict('os.environ', {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}):
            module.pre_request(None, request)
            self.assertIsNotNone(pending_timing.get())
            module.post_request(None, request, {}, None)
            self.assertIsNone(pending_timing.get())

    def test_application_exception_and_log_failure_still_reset_request_context(self):
        from config.capture_timing import WSGICaptureTiming, pending_timing

        module = self.module()
        request = SimpleNamespace(headers=[('X-Request-ID', 'a' * 32)], method='POST', path='/student/attempts/1/draft/')
        environ = {}
        def app(environ, start_response):
            raise RuntimeError('PRIVATE')
        with patch.dict('os.environ', {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}), \
                patch.object(module.logger, 'warning', side_effect=ValueError('log failed')):
            module.pre_request(None, request)
            with self.assertRaisesRegex(RuntimeError, 'PRIVATE'):
                WSGICaptureTiming(app)(environ, lambda status, headers: None)
            with self.assertRaisesRegex(ValueError, 'log failed'):
                module.post_request(SimpleNamespace(pid=321), request, environ, None)
            self.assertIsNone(pending_timing.get())

    def test_stage_b_worker_baseline_remains_sync_with_one_thread(self):
        module = self.module()
        self.assertEqual(module.worker_class, 'sync')
        self.assertEqual(module.threads, 1)

    def test_worker_resolves_routes_before_accepting_requests_without_database_access(self):
        module = self.module()
        clear_url_caches()
        resolver = get_resolver()
        self.assertNotIn('url_patterns', resolver.__dict__)
        module.post_worker_init(None)
        self.assertIn('url_patterns', resolver.__dict__)
        self.assertEqual(resolver.resolve('/health/').url_name, 'health')
