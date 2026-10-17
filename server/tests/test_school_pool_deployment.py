import configparser
from pathlib import Path
import re
from unittest import TestCase
from unittest.mock import patch
import os

from django.core.exceptions import ImproperlyConfigured

import yaml


DEPLOY = Path(__file__).resolve().parents[2] / 'deploy'
HTTP = {'web': (1, 'kt_core', '512M'), 'auth': (4, 'kt_auth', '1G'),
        'assessment': (6, 'kt_assessment', '3G'), 'startup': (2, 'kt_startup', '1G'),
        'capture': (4, 'kt_capture', '1280M'),
        'student-read': (2, 'kt_student', '768M'), 'teacher-read': (1, 'kt_teacher', '512M')}


def compose(name):
    loader = type('ComposeLoader', (yaml.SafeLoader,), {})
    for tag in ('!override', '!reset'):
        loader.add_constructor(tag, lambda loader, node: loader.construct_sequence(node))
    return yaml.load((DEPLOY / name).read_text(encoding='utf-8'), Loader=loader)['services']


class SchoolPoolDeploymentTests(TestCase):
    def admission_settings(self, environment):
        source = (DEPLOY.parent / 'server/config/settings.py').read_text(encoding='utf-8')
        block = source.split('ASSESSMENT_ADMISSION_ENABLED =', 1)[1].split('ATOMIC_SUBMISSION_ENABLED =', 1)[0]
        namespace = {'os': os, 'ImproperlyConfigured': ImproperlyConfigured}
        with patch.dict(os.environ, environment, clear=True):
            exec('ASSESSMENT_ADMISSION_ENABLED =' + block, namespace)
        return namespace

    def test_global_capacity_cannot_exceed_startup_budgets(self):
        enabled = {'ASSESSMENT_ADMISSION_ENABLED': '1', 'ASSESSMENT_ADMISSION_GLOBAL_ENABLED': '1'}
        for invalid in ({'ASSESSMENT_ADMISSION_CAPACITY': '3'}, {'ASSESSMENT_STARTUP_WORKERS': '8'},
                        {'ASSESSMENT_REMAINING_DB_CONNECTIONS': '7'}, {'ASSESSMENT_ADMISSION_RATE_PER_SECOND': '0'},
                        {'ASSESSMENT_ADMISSION_BATCH_SIZE': '257'}, {'ASSESSMENT_ADMISSION_TTL_SECONDS': '1'}):
            with self.subTest(invalid=invalid), self.assertRaises(ImproperlyConfigured):
                self.admission_settings(enabled | invalid)
        valid = self.admission_settings(enabled | {'ASSESSMENT_STARTUP_WORKERS': '3', 'ASSESSMENT_REMAINING_WORKERS': '5',
                                                   'ASSESSMENT_STARTUP_DB_CONNECTIONS': '4', 'ASSESSMENT_REMAINING_DB_CONNECTIONS': '4',
                                                   'ASSESSMENT_ADMISSION_CAPACITY': '3'})
        self.assertEqual(valid['ASSESSMENT_ADMISSION_CAPACITY'], 3)
        with self.assertRaises(ImproperlyConfigured):
            self.admission_settings({'ASSESSMENT_ADMISSION_GLOBAL_ENABLED': '1'})

    def test_six_http_pools_share_runtime_and_keep_fixed_resource_budget(self):
        services = compose('docker-compose.school.yml')
        for role, (workers, alias, memory) in HTTP.items():
            with self.subTest(role=role):
                config = services[role]
                configured = config['environment']['GUNICORN_WORKERS']
                default = re.fullmatch(r'\$\{[^:]+:-(\d+)\}', str(configured))
                self.assertEqual(int(default[1]) if default else configured, workers)
                self.assertEqual(config['environment']['POSTGRES_POOL_ALIAS'], alias)
                self.assertEqual(config['mem_limit'], memory)
                self.assertEqual(config['cpus'], 0)
                self.assertEqual(config['image'], services['web']['image'])
                self.assertEqual(config['build'], services['web']['build'])
                self.assertEqual(config['environment']['SESSION_REDIS_URL'], services['web']['environment']['SESSION_REDIS_URL'])
                self.assertEqual(config['environment']['DATABASE_CONN_MAX_AGE'], 0)
                self.assertEqual(config['environment']['SCHOOL_POOL_PROFILE'], 'six-pools-180-v1')
                self.assertEqual(config['environment']['DATABASE_DISABLE_SERVER_SIDE_CURSORS'], '1')
                self.assertNotIn('ports', config)
        self.assertEqual(sum(row[0] for row in HTTP.values()), 20)
        for role in HTTP:
            self.assertIn(role, services['nginx']['depends_on'])
        self.assertEqual(services['db']['mem_limit'], 0, 'Shared PostgreSQL must remain uncapped by school overlay')
        self.assertEqual(services['db']['cpus'], 0)

    def test_transaction_pool_budgets_force_one_database_user(self):
        template = configparser.ConfigParser(interpolation=None)
        template.read(DEPLOY / 'docker/pgbouncer-school.ini.template')
        expected = {'kt_core': (1, 0), 'kt_auth': (4, 0), 'kt_assessment': (6, 2), 'kt_startup': (2, 0),
                    'kt_capture': (4, 1), 'kt_student': (2, 0), 'kt_teacher': (1, 0),
                    'kt_background': (2, 0), 'kt_submission': (1, 0)}
        self.assertEqual(set(template['databases']), set(expected))
        for alias, (size, reserve) in expected.items():
            row = dict(part.split('=', 1) for part in template['databases'][alias].split())
            defaults = {'${ASSESSMENT_AUTH_WORKERS}': '4', '${ASSESSMENT_CAPTURE_WORKERS}': '4',
                        '${ASSESSMENT_STUDENT_READ_WORKERS}': '2'}
            row = {key: defaults.get(value, value) for key, value in row.items()}
            substitutions = {'${ASSESSMENT_STARTUP_DB_CONNECTIONS}': '2', '${ASSESSMENT_REMAINING_DB_CONNECTIONS}': '6'}
            self.assertEqual(int(substitutions.get(row['pool_size'], row['pool_size'])), size)
            self.assertEqual(int(row['reserve_pool_size']), reserve)
            self.assertEqual(int(substitutions.get(row['min_pool_size'], row['min_pool_size'])), size)
            self.assertEqual(row['user'], '${POSTGRES_USER}')
            self.assertEqual(row['dbname'], '${POSTGRES_DB}')
        self.assertEqual(sum(size + reserve for size, reserve in expected.values()), 26)
        self.assertEqual(template['pgbouncer']['pool_mode'], 'transaction')

    def test_routes_isolate_auth_critical_writes_and_event_uploads(self):
        config = (DEPLOY / 'nginx/kt-school.conf').read_text(encoding='utf-8')
        body = config.split('map "$request_method:$uri" $school_pool {', 1)[1].split('}', 1)[0]
        rows = re.findall(r'~(\^[^\s]+)\s+(\w+);', body)
        cases = [
            ('GET', '/api/v1/auth/bootstrap/', 'auth'), ('GET', '/api/v1/auth/csrf/', 'auth'),
            ('GET', '/api/v1/auth/session/', 'auth'), ('POST', '/api/v1/auth/login/', 'auth'),
            ('POST', '/api/v1/auth/logout/', 'auth'), ('POST', '/api/v1/auth/password/change/', 'auth'),
            ('POST', '/api/v1/auth/research-consent/', 'auth'),
            ('POST', '/api/v1/student/tasks/1/start/', 'startup'),
            ('POST', '/api/v1/student/tasks/1/resume/', 'startup'),
            ('POST', '/api/v1/student/tasks/1/admission/status/', 'startup'),
            ('GET', '/api/v1/student/tasks/1/admission/status/', 'core'),
            ('GET', '/api/v1/student/tasks/1/recovery/', 'assessment'),
            ('GET', '/api/v1/student/attempts/1/recovery/', 'assessment'),
            ('GET', '/api/v1/student/sessions/1/questions/2/recovery/', 'assessment'),
            ('POST', '/api/v1/student/sessions/1/questions/2/', 'assessment'),
            ('POST', '/api/v1/student/attempts/1/draft/', 'assessment'),
            ('POST', '/api/v1/student/attempts/1/submit/', 'assessment'),
            ('POST', '/api/v1/student/attempts/1/skip/', 'assessment'),
            ('POST', '/api/v1/student/sessions/1/finish/', 'assessment'),
            ('POST', '/api/v1/assessment/process/attempts/1/writer-lease/release/', 'assessment'),
            ('POST', '/api/v1/assessment/process/attempts/1/confidence/commit/', 'assessment'),
            ('POST', '/api/v1/assessment/process/capture-v2/attempts/1/tools/', 'assessment'),
            ('GET', '/api/v1/assessment/process/capture-v2/attempts/1/tools/', 'core'),
            ('POST', '/api/v1/assessment/process/capture-v2/attempts/1/resolve/', 'assessment'),
            ('POST', '/api/v1/assessment/process/capture-v2/batch/', 'capture'),
            ('POST', '/api/v1/assessment/process/events/batch/', 'capture'),
            ('POST', '/api/v1/assessment/process/capture-checkpoints/', 'capture'),
            ('POST', '/api/v1/assessment/process/time-sync/create/', 'capture'),
            ('GET', '/api/v1/student/courses/', 'student_read'),
            ('GET', '/api/v1/student/courses/1/tasks/', 'student_read'),
            ('GET', '/api/v1/student/sessions/1/summary/', 'student_read'),
            ('GET', '/api/v1/student/tasks/1/results/', 'student_read'),
            ('POST', '/api/v1/teacher/courses/1/data/export-jobs/', 'teacher_read'),
            ('POST', '/api/v1/auth/session/', 'core'),
            ('GET', '/api/v1/student/attempts/1/submit/', 'core'),
            ('POST', '/api/v1/student/attempts/1/submit/extra/', 'core'),
            ('GET', '/api/v1/assessment/process/events/batch/', 'core'),
            ('POST', '/api/v1/teacher/courses/1/classes/', 'core'),
        ]
        for method, path, expected in cases:
            with self.subTest(method=method, path=path):
                actual = next((pool for pattern, pool in rows if re.search(pattern, method + ':' + path)), 'core')
                self.assertEqual(actual, expected)
        for pool in ('auth', 'assessment', 'startup', 'capture'):
            self.assertRegex(config, rf'upstream {pool} \{{ server {pool}:8000; \}}')

    def test_test_overlay_keeps_all_six_pools_and_tunes_only_its_owned_postgres(self):
        isolated = compose('docker-compose.school-test.yml')
        for role, (workers, _, _) in HTTP.items():
            self.assertIn(role, isolated)
            self.assertNotIn('GUNICORN_WORKERS', isolated[role]['environment'], 'Do not override approved worker counts')
            self.assertEqual(isolated[role]['environment']['POSTGRES_DB'], 'kt_e2e')
        self.assertEqual(isolated['db']['mem_limit'], '4G')
        options = isolated['db']['command']
        for value in ('max_connections=64', 'shared_buffers=1GB', 'effective_cache_size=3GB',
                      'work_mem=4MB', 'maintenance_work_mem=128MB', 'autovacuum_work_mem=64MB',
                      'max_wal_size=2GB', 'checkpoint_completion_target=0.9',
                      'fsync=on', 'synchronous_commit=on', 'full_page_writes=on'):
            self.assertIn(value, options)
        shared = compose('docker-compose.school.yml')
        self.assertEqual(shared['db']['command'], ['postgres'])

    def test_status_operation_and_ciphertext_are_routed_explicitly(self):
        config = (DEPLOY / 'nginx/kt-school.conf').read_text(encoding='utf-8')
        body = config.split('map "$request_method:$uri" $capture_operation {', 1)[1].split('}', 1)[0]
        rows = re.findall(r'~(\^[^\s]+)\s+(\w+);', body)
        actual = next((op for pattern, op in rows if re.search(pattern, 'POST:/api/v1/student/tasks/1/admission/status/')), 'other')
        self.assertEqual(actual, 'admission_status')
        self.assertIn('location ^~ /assessment-content/ {', config)
        self.assertIn('alias /media/assessment-papers/;', config)

    def test_admission_is_experimental_and_persists_executing_capacity(self):
        services = compose('docker-compose.school.yml')
        env = services['startup']['environment']
        self.assertEqual(env['ASSESSMENT_ADMISSION_GLOBAL_ENABLED'], '${ASSESSMENT_ADMISSION_GLOBAL_ENABLED:-0}')
        self.assertEqual(env['ASSESSMENT_PREPARED_ENABLED'], '${ASSESSMENT_PREPARED_ENABLED:-0}')
        self.assertEqual(env['ASSESSMENT_ADMISSION_CAPACITY'], '${ASSESSMENT_ADMISSION_CAPACITY:-1}')
        redis = services['redis-admission']
        self.assertIn('noeviction', redis['command'])
        self.assertIn('always', redis['command'])
        self.assertIn('redis_admission_data:/data', redis['volumes'])

    def test_background_pressure_and_cpu_budget_leave_frontend_resources_available(self):
        services = compose('docker-compose.school.yml')
        background = ('scheduler', 'analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker', 'session-submission-worker')
        self.assertEqual(sum(services[role]['cpus'] for role in background), 2)
        self.assertEqual(sum(int(services[role]['mem_limit'][:-1]) * (1024 if services[role]['mem_limit'][-1] == 'G' else 1)
                             for role in background), 3072)
        for role in (*background, 'teacher-read'):
            self.assertEqual(services[role]['environment']['SCHOOL_RUNTIME_PRESSURE_FILE'], '/runtime-control/pressure.json')
            self.assertIn('runtime_control:/runtime-control:ro', services[role]['volumes'])
        submission = services['session-submission-worker']
        self.assertEqual(submission['command'], ['python', 'manage.py', 'run_session_submissions'])
        self.assertEqual(submission['environment']['POSTGRES_POOL_ALIAS'], 'kt_submission')
        self.assertEqual(submission['deploy']['replicas'], 1)
        self.assertIn('session-submission-worker', compose('docker-compose.school-test.yml'))
        self.assertIn('media_data:/app/media', services['publication-worker']['volumes'])

    def test_isolated_review_budget_preserves_roles_and_disables_unneeded_background(self):
        budget = compose('docker-compose.prepared-startup-isolated.yml')
        for role in ('analytics-worker', 'student-comparison-worker', 'export-worker'):
            self.assertEqual(budget[role]['profiles'], ['prepared-startup-background-disabled'])
        for role in ('scheduler', 'publication-worker', 'session-submission-worker'):
            self.assertNotIn('profiles', budget[role])
        self.assertEqual(budget['nginx']['ports'], ['127.0.0.1:${PREPARED_STARTUP_HTTP_PORT:-18081}:80'])
        self.assertEqual(budget['db']['mem_limit'], '1536M')
        for role in HTTP:
            self.assertNotIn('GUNICORN_WORKERS', budget[role].get('environment', {}))
