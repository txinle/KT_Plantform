import os
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from config.capture_timing import TIMING_ENV_KEY, WSGICaptureTiming, mark, timing_fields


class WSGICaptureTimingTests(SimpleTestCase):
    def test_light_aggregates_reach_wsgi_and_gunicorn_timing_fields(self):
        state = {'diagnostics_mode': 'light', 'capture_metrics': {'sql_count': 3, 'sql_seconds': .25,
                 'connection_seconds': .1, 'commit_seconds': .03, 'response_body_bytes': 12}}
        row = timing_fields(state)
        self.assertEqual(row.get('diagnostics_mode'), 'light')
        self.assertEqual(row.get('sql_count'), 3)
        self.assertEqual(row.get('sql_seconds'), .25)
        self.assertEqual(row.get('response_body_bytes'), 12)

    def test_disabled_wrapper_preserves_app_and_iterable_and_does_not_stamp(self):
        response = [b'ok']
        app = Mock(return_value=response)
        environ = {'PATH_INFO': '/student/attempts/1/draft/'}
        start_response = Mock()
        with patch.dict(os.environ, {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '0', 'POSTGRES_DB': 'production'}), \
                patch('config.capture_timing.time.perf_counter') as clock:
            self.assertIs(WSGICaptureTiming(app)(environ, start_response), response)
        app.assert_called_once_with(environ, start_response)
        clock.assert_not_called()
        self.assertNotIn(TIMING_ENV_KEY, environ)

    def test_wrapper_refuses_enable_outside_explicit_isolated_database(self):
        app = Mock()
        with patch.dict(os.environ, {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'production'}):
            with self.assertRaisesRegex(ValueError, 'kt_e2e'):
                WSGICaptureTiming(app)({}, Mock())
        app.assert_not_called()

    def test_wall_jump_does_not_change_monotonic_measurement(self):
        state = {}
        with patch('config.capture_timing.time.perf_counter', side_effect=[10, 10.2]), \
                patch('config.capture_timing.time.time', side_effect=[100, 110]):
            mark(state, 'gunicorn_pre')
            mark(state, 'gunicorn_end')
        state['nginx_ingress_at'] = 99.9
        row = timing_fields(state)
        self.assertTrue(row['clock_discontinuity'])
        self.assertAlmostEqual(row['clock_delta_error_seconds'], 9.8)
        self.assertIsNone(row['nginx_ingress_to_gunicorn_pre_wall_seconds'])
        self.assertTrue(row['nginx_wall_correlation_untrusted'])

    def test_response_close_and_iteration_failure_preserve_original_semantics(self):
        class Response:
            closed = False
            def __iter__(self):
                yield b'first'
                raise RuntimeError('PRIVATE ANSWER')
            def close(self):
                self.closed = True

        original = Response()
        environ = {'PATH_INFO': '/student/attempts/1/draft/'}
        with patch.dict(os.environ, {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}):
            response = WSGICaptureTiming(lambda environ, start_response: original)(environ, Mock())
            with self.assertRaisesRegex(RuntimeError, 'PRIVATE ANSWER'):
                list(response)
            response.close()
        self.assertTrue(original.closed)
        self.assertEqual(environ[TIMING_ENV_KEY]['error_type'], 'RuntimeError')
        self.assertIn('wsgi_complete', environ[TIMING_ENV_KEY]['marks'])

    def test_invalid_ingress_header_is_ignored_without_logging_it(self):
        for value in ['PRIVATE', 'nan', 'inf', '-1']:
            environ = {'HTTP_X_REQUEST_STARTED_AT': value}
            with patch.dict(os.environ, {'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED': '1', 'POSTGRES_DB': 'kt_e2e'}):
                list(WSGICaptureTiming(lambda environ, start_response: [b'ok'])(environ, Mock()))
            self.assertIsNone(timing_fields(environ[TIMING_ENV_KEY])['nginx_ingress_at'])
