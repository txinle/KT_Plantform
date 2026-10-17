"""The remediation candidate shares one bounded budget and rejects starts cheaply."""
import re
from pathlib import Path
from unittest import TestCase

import yaml


DEPLOY = Path(__file__).resolve().parents[2] / 'deploy'


def overlay():
    loader = type('CandidateLoader', (yaml.SafeLoader,), {})
    loader.add_constructor('!override', lambda loader, node: loader.construct_sequence(node))
    return yaml.load((DEPLOY / 'docker-compose.student-concurrency.yml').read_text(encoding='utf-8'), Loader=loader)


class StudentConcurrencyDeploymentTests(TestCase):
    def test_shared_parent_bounds_cpu_and_memory_without_small_child_cpu_quotas(self):
        services = overlay()['services']
        roles = ('db', 'pgbouncer', 'web', 'auth', 'startup', 'assessment', 'capture',
                 'student-read', 'teacher-read', 'nginx', 'scheduler', 'session-submission-worker')
        for role in roles:
            with self.subTest(role=role):
                self.assertEqual(services[role]['cgroup_parent'], 'ktconcurrency.slice')
                self.assertEqual(services[role]['cpus'], 0)
        unit = (DEPLOY / 'systemd/ktconcurrency.slice').read_text(encoding='utf-8')
        self.assertIn('CPUQuota=infinity', unit)
        self.assertIn('MemoryMax=infinity', unit)
        self.assertNotIn('CPUQuota=1400%', unit)
        self.assertNotIn('MemoryMax=12G', unit)
        for setting in ('shared_buffers=768MB', 'work_mem=4MB', 'max_connections=64',
                        'fsync=on', 'synchronous_commit=on', 'full_page_writes=on'):
            self.assertIn(setting, services['db']['command'])

    def test_candidate_A_declares_all_twenty_workers_and_keeps_nonurgent_jobs_out(self):
        services = overlay()['services']
        defaults = {'web': 1, 'auth': 4, 'startup': 4, 'assessment': 6,
                    'capture': 3, 'student-read': 1, 'teacher-read': 1}
        for role, count in defaults.items():
            value = str(services[role]['environment']['GUNICORN_WORKERS'])
            match = re.fullmatch(r'\$\{[^:]+:-(\d+)\}', value)
            self.assertEqual(int(match[1]) if match else int(value), count)
            self.assertEqual(services[role]['environment']['ASSESSMENT_PREPARED_ADMISSION_MODE'], 'postgres_transaction')
            self.assertEqual(str(services[role]['environment']['SCHOOL_REDIS_SESSION_ENABLED']), '0')
        self.assertEqual(sum(defaults.values()), 20)
        for role in ('analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker'):
            self.assertEqual(services[role]['profiles'], ['student-concurrency-nonurgent'])
        for role in ('scheduler', 'session-submission-worker'):
            self.assertNotIn('profiles', services[role])

    def test_nginx_start_limit_has_no_ip_bucket_or_worker_wait(self):
        config = (DEPLOY / 'nginx/kt-school.conf').read_text(encoding='utf-8')
        self.assertIn('limit_req_zone $prepared_start_key zone=prepared_start:1m rate=64r/s;', config)
        self.assertIn('limit_req zone=prepared_start burst=8 nodelay;', config)
        key_map = config.split('map "$request_method:$uri" $prepared_start_key {', 1)[1].split('}', 1)[0]
        self.assertRegex(key_map, r'POST:/api/v1/student/tasks/\[0-9\]\+/start/')
        self.assertNotIn('remote_addr', key_map)
        self.assertIn('location @prepared_start_busy', config)
        busy = config.split('location @prepared_start_busy', 1)[1].split('location ', 1)[0]
        self.assertIn('START_BUSY', busy)
        self.assertIn('Retry-After 1 always', busy)
        self.assertNotIn('proxy_pass', busy)
