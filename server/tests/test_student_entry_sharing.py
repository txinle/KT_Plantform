"""Exercise effective budgets and URL classification for the isolated shared entry."""
import configparser
import os
from pathlib import Path
import re
from unittest import TestCase
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / 'deploy'
SHARED = {
    'SCHOOL_POOL_PROFILE': 'shared-entry-v1',
    'ASSESSMENT_PREPARED_ADMISSION_MODE': 'postgres_transaction',
    'ASSESSMENT_ADMISSION_ENABLED': '1', 'ASSESSMENT_ADMISSION_GLOBAL_ENABLED': '1',
    'ASSESSMENT_ENTRY_WORKERS': '11', 'ASSESSMENT_ENTRY_DB_CONNECTIONS': '11',
    'ASSESSMENT_ENTRY_LOGIN_LIMIT': '8', 'ASSESSMENT_ENTRY_STARTUP_LIMIT': '8',
    'ASSESSMENT_ENTRY_HEAVY_LIMIT': '10', 'ASSESSMENT_ADMISSION_CAPACITY': '8',
    'ASSESSMENT_REMAINING_WORKERS': '4', 'ASSESSMENT_REMAINING_DB_CONNECTIONS': '4',
    'ASSESSMENT_CAPTURE_WORKERS': '3', 'ASSESSMENT_CAPTURE_DB_CONNECTIONS': '3',
}


def settings_for(environment):
    source = (ROOT / 'server/config/settings.py').read_text(encoding='utf-8')
    block = source.split('ASSESSMENT_ADMISSION_ENABLED =', 1)[1].split('ATOMIC_SUBMISSION_ENABLED =', 1)[0]
    namespace = {'os': os, 'ImproperlyConfigured': ImproperlyConfigured}
    with patch.dict(os.environ, environment, clear=True):
        exec('ASSESSMENT_ADMISSION_ENABLED =' + block, namespace)
    return namespace


def render(text, environment):
    def replace(match):
        value = environment[match[1]]
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', value):
            raise ValueError('Invalid or unexpanded configuration value')
        return value
    return re.sub(r'\$\{([A-Z_]+)\}', replace, text)


def route(text, method, path):
    body = text.split('map "$request_method:$uri" $school_pool {', 1)[1].split('}', 1)[0]
    rows = re.findall(r'~(\^[^\s]+)\s+(\w+);', body)
    return next((pool for pattern, pool in rows if re.search(pattern, method + ':' + path)), 'core')


class SharedEntrySettingsTests(TestCase):
    def test_twenty_and_twenty_four_http_workers_share_the_same_database_budget(self):
        for workers, login, heavy in ((11, 8, 10), (15, 12, 14)):
            with self.subTest(workers=workers):
                cfg = settings_for(SHARED | {'ASSESSMENT_ENTRY_WORKERS': str(workers),
                    'ASSESSMENT_ENTRY_LOGIN_LIMIT': str(login), 'ASSESSMENT_ENTRY_HEAVY_LIMIT': str(heavy)})
                self.assertEqual(cfg['ASSESSMENT_ENTRY_WORKERS'], workers)
                self.assertEqual(cfg['ASSESSMENT_ENTRY_DB_CONNECTIONS'], 11)
                self.assertEqual(cfg['ASSESSMENT_ADMISSION_CAPACITY'], 8)

    def test_rejects_missing_invalid_and_over_budget_allocations(self):
        invalid = [
            {'ASSESSMENT_ENTRY_WORKERS': '16'}, {'ASSESSMENT_ENTRY_DB_CONNECTIONS': '12'},
            {'ASSESSMENT_REMAINING_WORKERS': '3'}, {'ASSESSMENT_REMAINING_DB_CONNECTIONS': '3'},
            {'ASSESSMENT_CAPTURE_WORKERS': '2'}, {'ASSESSMENT_CAPTURE_DB_CONNECTIONS': '2'},
            {'ASSESSMENT_ADMISSION_CAPACITY': '9'}, {'ASSESSMENT_ENTRY_HEAVY_LIMIT': '11'},
            {'ASSESSMENT_ENTRY_LOGIN_LIMIT': '11'}, {'ASSESSMENT_ENTRY_STARTUP_LIMIT': '0'},
            {'ASSESSMENT_PREPARED_ADMISSION_MODE': 'redis'}, {'ASSESSMENT_ENTRY_WORKERS': '${WORKERS}'},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises((ImproperlyConfigured, ValueError)):
                settings_for(SHARED | values)
        for key in ('ASSESSMENT_ENTRY_WORKERS', 'ASSESSMENT_ENTRY_DB_CONNECTIONS',
                    'ASSESSMENT_ENTRY_LOGIN_LIMIT', 'ASSESSMENT_ENTRY_STARTUP_LIMIT', 'ASSESSMENT_ENTRY_HEAVY_LIMIT'):
            with self.subTest(missing=key), self.assertRaises((ImproperlyConfigured, ValueError)):
                settings_for({k: v for k, v in SHARED.items() if k != key})

    def test_split_A_B_C_still_use_the_original_validation(self):
        for auth, startup, save in ((4, 4, 6), (4, 6, 4), (6, 4, 4)):
            cfg = settings_for({'ASSESSMENT_PREPARED_ADMISSION_MODE': 'postgres_transaction',
                'ASSESSMENT_AUTH_WORKERS': str(auth), 'ASSESSMENT_STARTUP_WORKERS': str(startup),
                'ASSESSMENT_STARTUP_DB_CONNECTIONS': str(startup), 'ASSESSMENT_REMAINING_WORKERS': str(save),
                'ASSESSMENT_REMAINING_DB_CONNECTIONS': str(save), 'ASSESSMENT_CAPTURE_WORKERS': '3',
                'ASSESSMENT_STUDENT_READ_WORKERS': '1', 'ASSESSMENT_ADMISSION_CAPACITY': str(startup)})
            self.assertEqual(cfg['ASSESSMENT_STARTUP_WORKERS'], startup)


class SharedEntryDeploymentTests(TestCase):
    def test_database_template_has_twenty_three_regular_and_three_reserve_connections(self):
        environment = SHARED | {'POSTGRES_DB': 'kt_e2e', 'POSTGRES_USER': 'kt_user', 'PGBOUNCER_SERVER_HOST': 'db'}
        text = (DEPLOY / 'docker/pgbouncer-school-shared.ini.template').read_text(encoding='utf-8')
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(render(text, environment))
        rows = {alias: dict(part.split('=', 1) for part in value.split())
                for alias, value in config['databases'].items()}
        self.assertEqual(set(rows), {'kt_entry', 'kt_assessment', 'kt_capture', 'kt_core',
                                    'kt_teacher', 'kt_background', 'kt_submission'})
        self.assertEqual(sum(int(row['pool_size']) for row in rows.values()), 23)
        self.assertEqual(sum(int(row['reserve_pool_size']) for row in rows.values()), 3)
        self.assertEqual(rows['kt_entry']['pool_size'], '11')
        self.assertTrue(all(row['pool_size'] == row['min_pool_size'] for row in rows.values()))
        self.assertEqual(config['pgbouncer']['pool_mode'], 'transaction')
        self.assertEqual(config['pgbouncer']['reserve_pool_timeout'], '3')
        with self.assertRaises(KeyError):
            render(text, {k: v for k, v in environment.items() if k != 'ASSESSMENT_ENTRY_DB_CONNECTIONS'})
        with self.assertRaises(ValueError):
            render(text, environment | {'ASSESSMENT_ENTRY_DB_CONNECTIONS': '${MISSING}'})

    def test_shared_overlay_disables_the_three_old_entry_services(self):
        loader = type('SharedLoader', (yaml.SafeLoader,), {})
        loader.add_constructor('!override', lambda loader, node: loader.construct_mapping(node)
                               if isinstance(node, yaml.MappingNode) else loader.construct_sequence(node))
        overlay = yaml.load((DEPLOY / 'docker-compose.student-entry-sharing.yml').read_text(encoding='utf-8'), Loader=loader)
        services = overlay['services']
        self.assertEqual(services['entry']['mem_limit'], '3456M')
        self.assertEqual(services['entry']['environment']['POSTGRES_POOL_ALIAS'], 'kt_entry')
        for name in ('auth', 'startup', 'student-read'):
            self.assertEqual(services[name]['profiles'], ['student-entry-split-disabled'])
            self.assertNotIn(name, services['nginx']['depends_on'])
        self.assertIn('entry', services['nginx']['depends_on'])
        self.assertEqual(services['nginx']['environment']['NGINX_ENVSUBST_FILTER'], '^ASSESSMENT_ENTRY_')
        self.assertEqual(set(services['nginx']['volumes']), {
            './nginx/kt-school-shared-entry.conf.template:/etc/nginx/templates/default.conf.template:ro',
            './nginx/frontend-pages.conf:/etc/nginx/frontend-pages.conf:ro',
            'backend_static:/backend-static:ro', 'frontend_assets:/static/spa-assets/assets', 'media_data:/media:ro'})

    def test_real_submission_urls_and_invalid_neighbours_are_classified_by_method(self):
        from django.urls import reverse
        # Settings/URL setup is supplied by manage.py test; no database queries are needed.
        path = reverse('assessment_api:student-session-submission', args=[123])
        self.assertEqual(path, '/api/v1/student/sessions/123/submission/')
        for filename, read_pool in (('kt-school.conf', 'student_read'),
                                    ('kt-school-shared-entry.conf.template', 'entry')):
            text = (DEPLOY / 'nginx' / filename).read_text(encoding='utf-8')
            self.assertEqual(route(text, 'POST', path), 'assessment')
            for method in ('GET', 'HEAD'):
                self.assertEqual(route(text, method, path), read_pool)
            for method, neighbour in (('DELETE', path), ('GET', path[:-1]), ('GET', path + 'extra/'),
                                      ('POST', path.replace('123', 'abc'))):
                self.assertEqual(route(text, method, neighbour), 'core')

    def test_shared_nginx_preserves_read_and_write_routes_and_uses_global_limits(self):
        text = (DEPLOY / 'nginx/kt-school-shared-entry.conf.template').read_text(encoding='utf-8')
        cases = [('POST', '/api/v1/auth/login/', 'entry'),
                 ('POST', '/api/v1/student/tasks/1/start/', 'entry'),
                 ('POST', '/api/v1/student/tasks/1/admission/status/', 'entry'),
                 ('GET', '/api/v1/student/courses/1/tasks/', 'entry'),
                 ('POST', '/api/v1/student/attempts/1/draft/', 'assessment'),
                 ('POST', '/api/v1/student/sessions/1/finish/', 'assessment'),
                 ('POST', '/api/v1/assessment/process/capture-v2/batch/', 'capture')]
        for method, path, expected in cases:
            self.assertEqual(route(text, method, path), expected)
        for key in ('entry_login_key', 'entry_startup_key', 'entry_heavy_key'):
            body = text.split(f' ${key} {{', 1)[1].split('}', 1)[0]
            self.assertNotIn('remote_addr', body)
        for code in ('LOGIN_BUSY', 'START_BUSY'):
            self.assertIn(code, text)
        self.assertIn('limit_req zone=prepared_start burst=8 nodelay;', text)
        self.assertIn('limit_conn_status 429;', text)
        self.assertIn('private, max-age=604800, immutable', text)
