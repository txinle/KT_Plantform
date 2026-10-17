"""Capacity experiments retain old profiles and enforce effective DB budgets."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
from unittest import TestCase

from django.core.exceptions import ImproperlyConfigured
import yaml

from .test_student_entry_sharing import DEPLOY, SHARED, render, route, settings_for


CAPACITY = SHARED | {
    'SCHOOL_POOL_PROFILE': 'shared-entry-v2',
    'ASSESSMENT_ENTRY_WORKERS': '15', 'ASSESSMENT_ENTRY_LOGIN_LIMIT': '12',
    'ASSESSMENT_ENTRY_HEAVY_LIMIT': '14', 'ASSESSMENT_REMAINING_WORKERS': '8',
    'ASSESSMENT_REMAINING_DB_CONNECTIONS': '8',
    'ASSESSMENT_SUBMISSION_WORKERS': '4', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '4',
    'ASSESSMENT_ANALYTICS_WORKERS': '1', 'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '2',
    'ASSESSMENT_ENTRY_STARTUP_BURST': '8', 'ASSESSMENT_ENTRY_STARTUP_RATE': '64',
    'SCHOOL_DB_MAX_CONNECTIONS': '64', 'SCHOOL_DB_RESERVED_CONNECTIONS': '3',
    'SCHOOL_DB_DIRECT_CONNECTIONS': '1', 'SCHOOL_DB_MONITOR_CONNECTIONS': '2',
    'SCHOOL_DB_MAINTENANCE_CONNECTIONS': '2', 'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS': '8',
}


CAPACITY_V3 = CAPACITY | {
    'SCHOOL_POOL_PROFILE': 'shared-entry-v3',
    'ASSESSMENT_REMAINING_WORKERS': '12',
    'ASSESSMENT_SUBMISSION_WORKERS': '1', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '1',
    'ASSESSMENT_CORE_WORKERS': '1', 'ASSESSMENT_CORE_DB_CONNECTIONS': '1',
    'ASSESSMENT_TEACHER_WORKERS': '1', 'ASSESSMENT_TEACHER_DB_CONNECTIONS': '1',
    'PGBOUNCER_MAX_CLIENT_CONN': '200',
    'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS': '0',
    **{f'ASSESSMENT_{role}_DB_RESERVE_CONNECTIONS': str(reserve) for role, reserve in (
        ('ENTRY', 0), ('REMAINING', 2), ('CAPTURE', 1), ('CORE', 0),
        ('TEACHER', 0), ('BACKGROUND', 0), ('SUBMISSION', 0))},
}


class CapacityV3Tests(TestCase):
    def validate_shell(self, values):
        sh = shutil.which('sh') or r'C:\Program Files\Git\bin\sh.exe'
        if not Path(sh).exists():
            self.skipTest('POSIX shell unavailable')
        source = (DEPLOY / 'docker/pgbouncer-entrypoint.sh').read_text().split('# Connection identifiers', 1)[0]
        return subprocess.run([sh, '-s'], input=source + '\nexit 0\n', text=True,
            env=os.environ | CAPACITY_V3 | {'SCHOOL_RUNTIME_ISOLATION_ENABLED': '1',
                'POSTGRES_DB': 'kt_e2e', 'POSTGRES_USER': 'kt_user', 'POSTGRES_PASSWORD': 'test'} | values,
            capture_output=True)

    def test_v3_accepts_dynamic_workers_pools_rates_and_database_budget(self):
        values = {'ASSESSMENT_ENTRY_WORKERS': '131', 'ASSESSMENT_ENTRY_DB_CONNECTIONS': '130',
            'ASSESSMENT_ENTRY_LOGIN_LIMIT': '129', 'ASSESSMENT_ENTRY_STARTUP_LIMIT': '128',
            'ASSESSMENT_ENTRY_HEAVY_LIMIT': '130', 'ASSESSMENT_ADMISSION_CAPACITY': '128',
            'ASSESSMENT_REMAINING_WORKERS': '21', 'ASSESSMENT_REMAINING_DB_CONNECTIONS': '17',
            'ASSESSMENT_CAPTURE_WORKERS': '23', 'ASSESSMENT_CAPTURE_DB_CONNECTIONS': '21',
            'ASSESSMENT_CORE_WORKERS': '2', 'ASSESSMENT_CORE_DB_CONNECTIONS': '2',
            'ASSESSMENT_TEACHER_WORKERS': '3', 'ASSESSMENT_TEACHER_DB_CONNECTIONS': '2',
            'ASSESSMENT_SUBMISSION_WORKERS': '5', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '6',
            'ASSESSMENT_ANALYTICS_WORKERS': '3', 'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '6',
            'ASSESSMENT_ENTRY_STARTUP_RATE': '129', 'ASSESSMENT_ENTRY_STARTUP_BURST': '129',
            'SCHOOL_DB_MAX_CONNECTIONS': '256', 'ASSESSMENT_CORE_DB_RESERVE_CONNECTIONS': '2'}
        cfg = settings_for(CAPACITY_V3 | values)
        self.assertEqual(cfg['ASSESSMENT_CORE_WORKERS'], 2)
        result = self.validate_shell(values)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_v3_settings_and_pooler_reject_invalid_relations(self):
        for values in ({'ASSESSMENT_CAPTURE_WORKERS': '0'}, {'ASSESSMENT_CORE_DB_CONNECTIONS': '0'},
                {'ASSESSMENT_TEACHER_DB_RESERVE_CONNECTIONS': '-1'}, {'PGBOUNCER_MAX_CLIENT_CONN': '0'},
                {'ASSESSMENT_ENTRY_HEAVY_LIMIT': '15'}, {'ASSESSMENT_ENTRY_LOGIN_LIMIT': '15'},
                {'ASSESSMENT_ADMISSION_CAPACITY': '9'}, {'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '1'},
                {'ASSESSMENT_SUBMISSION_WORKERS': '2'}, {'SCHOOL_DB_MAX_CONNECTIONS': '20'},
                {'ASSESSMENT_ENTRY_DB_RESERVE_CONNECTIONS': '40'}):
            with self.subTest(values=values):
                with self.assertRaises((ImproperlyConfigured, ValueError)):
                    settings_for(CAPACITY_V3 | values)
                self.assertNotEqual(self.validate_shell(values).returncode, 0)

    def test_v3_template_renders_dynamic_core_teacher_and_reserves(self):
        env = CAPACITY_V3 | {'POSTGRES_DB': 'kt_e2e', 'POSTGRES_USER': 'kt_user',
            'PGBOUNCER_SERVER_HOST': 'db', 'ASSESSMENT_CORE_DB_CONNECTIONS': '3',
            'ASSESSMENT_TEACHER_DB_CONNECTIONS': '4', 'ASSESSMENT_CORE_DB_RESERVE_CONNECTIONS': '2',
            'PGBOUNCER_MAX_CLIENT_CONN': '401'}
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(render((DEPLOY / 'docker/pgbouncer-school-capacity.ini.template').read_text(), env))
        self.assertIn('pool_size=3 min_pool_size=3 reserve_pool_size=2', config['databases']['kt_core'])
        self.assertIn('pool_size=4 min_pool_size=4', config['databases']['kt_teacher'])
        self.assertEqual(config['pgbouncer']['max_client_conn'], '401')


class CapacitySettingsTests(TestCase):
    def test_all_approved_save_and_submission_allocations_are_available(self):
        for workers, connections in ((4, 4), (8, 4), (8, 8), (12, 8), (12, 12)):
            for submissions in (1, 2, 4):
                with self.subTest(workers=workers, connections=connections, submissions=submissions):
                    cfg = settings_for(CAPACITY | {
                        'ASSESSMENT_REMAINING_WORKERS': str(workers),
                        'ASSESSMENT_REMAINING_DB_CONNECTIONS': str(connections),
                        'ASSESSMENT_SUBMISSION_WORKERS': str(submissions),
                        'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': str(submissions)})
                    self.assertEqual(cfg['SCHOOL_POOL_PROFILE'], 'shared-entry-v2')

    def test_extended_entry_and_two_analytics_workers_fit_the_fixed_database(self):
        cfg = settings_for(CAPACITY | {'ASSESSMENT_ENTRY_WORKERS': '19',
            'ASSESSMENT_ENTRY_DB_CONNECTIONS': '15', 'ASSESSMENT_ENTRY_HEAVY_LIMIT': '18',
            'ASSESSMENT_ENTRY_LOGIN_LIMIT': '16', 'ASSESSMENT_ENTRY_STARTUP_LIMIT': '12',
            'ASSESSMENT_ADMISSION_CAPACITY': '12', 'ASSESSMENT_ANALYTICS_WORKERS': '2',
            'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '4', 'ASSESSMENT_ENTRY_STARTUP_BURST': '32'})
        self.assertEqual(cfg['ASSESSMENT_ENTRY_WORKERS'], 19)

    def test_settings_and_pgbouncer_reject_the_same_invalid_allocations(self):
        invalid = [
            {'ASSESSMENT_REMAINING_WORKERS': '13'}, {'ASSESSMENT_REMAINING_DB_CONNECTIONS': '13'},
            {'ASSESSMENT_SUBMISSION_WORKERS': '5'}, {'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '3'},
            {'ASSESSMENT_ANALYTICS_WORKERS': '2'}, {'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '1'},
            {'ASSESSMENT_ENTRY_HEAVY_LIMIT': '15'}, {'ASSESSMENT_ADMISSION_CAPACITY': '12'},
            {'SCHOOL_DB_MAX_CONNECTIONS': '128'}, {'SCHOOL_DB_RESERVED_CONNECTIONS': '0'},
            {'SCHOOL_DB_DIRECT_CONNECTIONS': '0'}, {'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS': '40'},
            {'ASSESSMENT_ENTRY_STARTUP_BURST': '33'}, {'ASSESSMENT_ENTRY_STARTUP_RATE': '0'},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises((ImproperlyConfigured, ValueError)):
                settings_for(CAPACITY | values)
        sh = shutil.which('sh') or r'C:\Program Files\Git\bin\sh.exe'
        if not Path(sh).exists():
            self.skipTest('POSIX shell unavailable for deployment validation')
        validation = (DEPLOY / 'docker/pgbouncer-entrypoint.sh').read_text(encoding='utf-8').split(
            '# Connection identifiers', 1)[0] + '\nexit 0\n'
        base = os.environ | CAPACITY | {'SCHOOL_RUNTIME_ISOLATION_ENABLED': '1',
            'POSTGRES_DB': 'kt_e2e', 'POSTGRES_USER': 'kt_user', 'POSTGRES_PASSWORD': 'test'}
        for values, expected in (({}, 0), *((values, 1) for values in invalid)):
            result = subprocess.run([sh, '-s'], input=validation, env=base | values, capture_output=True, text=True)
            with self.subTest(shell=values):
                self.assertEqual(result.returncode, expected, result.stderr)


class CapacityDeploymentTests(TestCase):
    def test_parameterized_database_template_and_ingress_preserve_routes(self):
        environment = CAPACITY_V3 | CAPACITY | {'POSTGRES_DB': 'kt_e2e', 'POSTGRES_USER': 'kt_user', 'PGBOUNCER_SERVER_HOST': 'db'}
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(render((DEPLOY / 'docker/pgbouncer-school-capacity.ini.template').read_text(), environment))
        rows = {name: dict(part.split('=', 1) for part in value.split())
                for name, value in config['databases'].items()}
        self.assertEqual(sum(int(row['pool_size']) for row in rows.values()), 30)
        self.assertEqual(sum(int(row['reserve_pool_size']) for row in rows.values()), 3)
        self.assertEqual(rows['kt_submission']['pool_size'], '4')
        nginx = (DEPLOY / 'nginx/kt-school-capacity.conf.template').read_text(encoding='utf-8')
        rendered = render(nginx, environment)
        self.assertIn('rate=64r/s;', rendered)
        self.assertIn('burst=8 nodelay;', rendered)
        self.assertIn('burst=32 nodelay;', render(nginx, environment | {'ASSESSMENT_ENTRY_STARTUP_BURST': '32'}))
        self.assertEqual(route(rendered, 'POST', '/api/v1/student/attempts/1/draft/'), 'assessment')

    def test_overlay_parameters_keep_legacy_defaults_and_allow_v3_resource_sizing(self):
        text = (DEPLOY / 'docker-compose.student-capacity.yml').read_text()
        self.assertIn('SCHOOL_POOL_PROFILE: ${SCHOOL_POOL_PROFILE:-shared-entry-v2}', text)
        self.assertIn('"max_connections=${SCHOOL_DB_MAX_CONNECTIONS:-64}"', text)
        for role in ('ENTRY', 'REMAINING', 'CAPTURE', 'CORE', 'TEACHER'):
            self.assertIn('${ASSESSMENT_' + role + '_WORKERS:-', text)
            self.assertIn('${ASSESSMENT_' + role + '_MEMORY_LIMIT:-', text)
        self.assertIn('${PGBOUNCER_MAX_CLIENT_CONN:-200}', text)

    def test_overlay_enables_statistics_and_scales_unthrottled_consumers(self):
        loader = type('CapacityLoader', (yaml.SafeLoader,), {})
        loader.add_constructor('!override', lambda loader, node: loader.construct_mapping(node)
                               if isinstance(node, yaml.MappingNode) else loader.construct_sequence(node))
        overlay = yaml.load((DEPLOY / 'docker-compose.student-capacity.yml').read_text(), Loader=loader)
        services = overlay['services']
        self.assertEqual(services['analytics-worker']['profiles'], [])
        self.assertEqual(services['analytics-worker']['environment']['ADVANCED_DATA_MATERIALIZED_ENABLED'], '1')
        self.assertEqual(services['session-submission-worker']['deploy']['replicas'], '${ASSESSMENT_SUBMISSION_WORKERS:-4}')
        for service in ('session-submission-worker', 'analytics-worker'):
            self.assertEqual(services[service]['cpus'], 0)
            self.assertEqual(services[service]['deploy']['resources']['limits']['cpus'], '0')
        for service in ('entry', 'assessment', 'capture', 'web', 'teacher-read'):
            self.assertEqual(services[service]['environment']['DATABASE_CONN_MAX_AGE'], '${SCHOOL_HTTP_CONN_MAX_AGE:-0}')
            self.assertEqual(services[service]['environment']['DATABASE_DISABLE_SERVER_SIDE_CURSORS'], '1')
        self.assertEqual(services['scheduler']['environment']['DATABASE_CONN_MAX_AGE'], 0)
        self.assertEqual(services['scheduler']['environment']['POSTGRES_HOST'], 'db')
        self.assertEqual(services['scheduler']['environment']['POSTGRES_POOL_ALIAS'], '')
