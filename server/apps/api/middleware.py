import uuid
import hashlib
import json
import logging
import os
import re
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection

from config.capture_timing import TIMING_ENV_KEY, coarse_path, diagnostics_mode, finish_interval, mark, timing_fields


logger = logging.getLogger('kt.capture_diagnostics')
connection_logger = logging.getLogger('kt.assessment_connection')
ASSESSMENT_PATHS = ('/api/v1/student/', '/api/v1/assessment/process/', '/student/sessions/', '/assessment/process/capture-v2/offline-worker.js')


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.capture_diagnostics = getattr(settings, 'SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED', False)
        if self.capture_diagnostics and os.getenv('POSTGRES_DB') != 'kt_e2e':
            raise ImproperlyConfigured('Capture diagnostics requires the isolated kt_e2e database')
        try:
            self.capture_diagnostics_mode = diagnostics_mode() if self.capture_diagnostics else None
        except ValueError as error:
            raise ImproperlyConfigured(str(error)) from error

    def __call__(self, request):
        if self.capture_diagnostics and request.path.startswith(ASSESSMENT_PATHS):
            state = request.META.get(TIMING_ENV_KEY)
            if state is None:
                state = {}
                request.META[TIMING_ENV_KEY] = state
            mark(state, 'middleware_start')
        request.request_id = uuid.uuid4().hex
        if self.capture_diagnostics and request.path.startswith(ASSESSMENT_PATHS):
            supplied = request.headers.get('X-Request-ID', '')
            if re.fullmatch('[a-f0-9]{32}', supplied):
                request.request_id = supplied
            response = self.profile_capture(request)
        else:
            response = self.assessment_response(request)
        if request.path.startswith("/api/v1/"):
            from .response import apply_api_envelope
            response = apply_api_envelope(request, response)
            response["X-Request-ID"] = request.request_id
        return response

    def assessment_response(self, request):
        if not getattr(settings, 'ASSESSMENT_CONNECT_RETRY_ENABLED', False) or not request.path.startswith(ASSESSMENT_PATHS):
            return self.get_response(request)
        original_connect = connection.get_new_connection
        remaining = 1

        def connect(parameters):
            nonlocal remaining
            started = time.perf_counter()
            try:
                return original_connect(parameters)
            except (OperationalError, connection.Database.OperationalError) as error:
                sqlstate = getattr(error, 'sqlstate', None) or getattr(error.__cause__, 'sqlstate', None)
                if not remaining or (sqlstate is not None and not (sqlstate.startswith('08') or sqlstate in {'57P01', '57P02', '57P03'})):
                    raise
                remaining -= 1
                operation_id = request.headers.get('X-KT-Operation-ID', '')
                connection_logger.warning(json.dumps({
                    'request_id': request.request_id,
                    'operation_id': operation_id if re.fullmatch('[a-f0-9]{32}', operation_id) else None,
                    'path': re.sub(r'/\d+(?=/|$)', '/:id', request.path),
                    'method': request.method, 'retry_limit': 1,
                    'error_type': type(error).__name__, 'sqlstate': sqlstate,
                    'failed_connect_seconds': round(time.perf_counter() - started, 6),
                }, separators=(',', ':')))
                return original_connect(parameters)

        try:
            connection.get_new_connection = connect
            return self.get_response(request)
        finally:
            connection.get_new_connection = original_connect

    def profile_capture(self, request):
        state = request.META[TIMING_ENV_KEY]
        state['diagnostics_mode'] = self.capture_diagnostics_mode
        started, started_at = state['marks']['middleware_start']
        operation_id = request.headers.get('X-KT-Operation-ID', '')
        row = {'phase': 'middleware_request_complete', 'request_id': request.request_id,
               'operation_id': operation_id if re.fullmatch('[a-f0-9]{32}', operation_id) else None,
               'path': coarse_path(request.path),
               'method': request.method, 'started_at': started_at, 'queries': [], 'commits': [],
               'rollbacks': [], 'connections': [], 'worker_pid': os.getpid(), 'commit_hooks_seconds': 0, 'commit_hooks': [],
               'transactions': {'savepoints': 0, 'savepoint_releases': 0, 'savepoint_rollbacks': 0, 'autocommit_statements': 0},
               'implicit_commit_timing': 'included_in_autocommit_statement', 'status': None, 'error_type': None}
        detailed = self.capture_diagnostics_mode == 'sql'
        row['diagnostics_mode'] = self.capture_diagnostics_mode
        row['sql_timing_scope'] = 'driver_execute_including_waits_not_exact_lock_time'
        row['request_body_bytes'] = int(request.META.get('CONTENT_LENGTH', '')) if str(request.META.get('CONTENT_LENGTH', '')).isdigit() else None
        row['response_body_bytes'] = None
        for phase in ('connection', 'sql', 'commit', 'rollback', 'commit_hooks'):
            row[phase + '_count'] = 0
            row[phase + '_seconds'] = 0
        lists = {'connection': 'connections', 'sql': 'queries', 'commit': 'commits',
                 'rollback': 'rollbacks', 'commit_hooks': 'commit_hooks'}

        def record(phase, stamp, detail):
            seconds = finish_interval(detail, stamp, state, phase) if detailed else time.perf_counter() - stamp
            row[phase + '_count'] += 1
            row[phase + '_seconds'] += seconds
            if detailed:
                row[lists[phase]].append(detail)

        backend_pid = None
        original_commit = connection.commit
        original_rollback = connection.rollback
        original_hooks = connection.run_and_clear_commit_hooks
        original_connect = connection.get_new_connection

        def connect(parameters):
            stamp = time.perf_counter()
            detail = {'started_at': time.time(), 'successful': False, 'error_type': None} if detailed else {}
            try:
                result = original_connect(parameters)
                detail['successful'] = True
                return result
            except Exception as error:
                detail['error_type'] = type(error).__name__
                raise
            finally:
                record('connection', stamp, detail)

        def rollback():
            nonlocal backend_pid
            stamp = time.perf_counter()
            detail = {'started_at': time.time()} if detailed else {}
            try:
                return original_rollback()
            finally:
                record('rollback', stamp, detail)
                backend_pid = None

        def commit_hooks():
            stamp = time.perf_counter()
            detail = {'started_at': time.time()} if detailed else {}
            try:
                return original_hooks()
            finally:
                record('commit_hooks', stamp, detail)

        def commit():
            nonlocal backend_pid
            stamp = time.perf_counter()
            detail = {'started_at': time.time(), 'backend_pid': backend_pid} if detailed else {}
            try:
                return original_commit()
            finally:
                record('commit', stamp, detail)
                backend_pid = None

        def query(execute, sql, params, many, context):
            nonlocal backend_pid
            if detailed and backend_pid is None and connection.vendor == 'postgresql' and connection.in_atomic_block:
                execute('SELECT pg_backend_pid()', None, False, context)
                backend_pid = context['cursor'].fetchone()[0]
            normalized = re.sub(r'\s+', ' ', sql).strip() if detailed else sql.lstrip()
            operation = normalized.split(None, 1)[0].lower() if normalized else ''
            if operation == 'savepoint':
                row['transactions']['savepoints'] += 1
            elif operation == 'release':
                row['transactions']['savepoint_releases'] += 1
            elif operation == 'rollback' and ' TO ' in normalized.upper():
                row['transactions']['savepoint_rollbacks'] += 1
            autocommit = connection.get_autocommit() and not connection.in_atomic_block
            if autocommit:
                row['transactions']['autocommit_statements'] += 1
            stamp = time.perf_counter()
            detail = {'fingerprint': hashlib.md5(normalized.encode()).hexdigest()[:16],
                      'operation': operation, 'autocommit': autocommit, 'started_at': time.time(),
                      'relations': sorted(set(re.findall(r'\b((?:assessment|analytics|experiments|accounts)_[a-z_]+)\b', sql))),
                      'backend_pid': backend_pid if connection.in_atomic_block else None} if detailed else {}
            try:
                return execute(sql, params, many, context)
            finally:
                record('sql', stamp, detail)

        try:
            connection.commit = commit
            connection.rollback = rollback
            connection.run_and_clear_commit_hooks = commit_hooks
            connection.get_new_connection = connect
            with connection.execute_wrapper(query):
                response = self.assessment_response(request)
            row['status'] = response.status_code
            if not response.streaming:
                row['response_body_bytes'] = len(response.content)
            response['X-Capture-Seconds'] = f'{time.perf_counter() - started:.6f}'
            return response
        except Exception as error:
            row['error_type'] = type(error).__name__
            raise
        finally:
            connection.commit = original_commit
            connection.rollback = original_rollback
            connection.run_and_clear_commit_hooks = original_hooks
            connection.get_new_connection = original_connect
            mark(state, 'middleware_end')
            row['seconds'] = round(state['marks']['middleware_end'][0] - started, 6)
            row['ended_at'] = state['marks']['middleware_end'][1]
            database_seconds = sum(row[phase + '_seconds'] for phase in ('connection', 'sql', 'commit', 'rollback'))
            row['application_excluding_measured_database_seconds'] = round(max(0, row['seconds'] - database_seconds), 6)
            for phase in ('connection', 'sql', 'commit', 'rollback', 'commit_hooks'):
                row[phase + '_seconds'] = round(row[phase + '_seconds'], 6)
            state['capture_metrics'] = {key: row[key] for key in (
                'connection_count', 'connection_seconds', 'sql_count', 'sql_seconds',
                'commit_count', 'commit_seconds', 'rollback_count', 'rollback_seconds',
                'commit_hooks_count', 'commit_hooks_seconds', 'request_body_bytes', 'response_body_bytes',
                'application_excluding_measured_database_seconds', 'sql_timing_scope')}
            row.update(timing_fields(state))
            logger.warning(json.dumps(row, separators=(',', ':')))
