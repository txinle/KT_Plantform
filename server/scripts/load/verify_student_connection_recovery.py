"""Verify warmed HTTP DB connections across two planned isolated restarts.

No pressure driver may run during this maintenance probe. The same authenticated
cookie survives each restart; HTTP entry processes must remain unchanged.
"""
import argparse
import copy
import csv
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
from prepare_student_capacity_remote import ROOT, PROJECT, PORT
from run_student_capacity_matrix import validate_compose, write_once
from student_concurrency_protocol import HttpClient, checked, digest

HTTP_ROLES = {'entry', 'assessment', 'capture', 'web', 'teacher-read'}
DEPENDENT_ROLES = {'scheduler', 'analytics-worker', 'session-submission-worker'}


def validate_target(config, compose_path, fixture_path, output_path):
    validate_compose(config, compose_path)
    for path in (fixture_path, output_path):
        if not Path(path).resolve().is_relative_to((ROOT / 'evidence').resolve()):
            raise ValueError('Recovery inputs and outputs must stay inside owned evidence')
    for role in HTTP_ROLES & set(config['services']):
        env = config['services'][role]['environment']
        if (env.get('SCHOOL_POOL_PROFILE') != 'shared-entry-v2'
                or str(env.get('DATABASE_CONN_MAX_AGE')) != '60'
                or str(env.get('DATABASE_DISABLE_SERVER_SIDE_CURSORS')) != '1'):
            raise ValueError('Recovery requires the explicitly configured reuse60 transaction-pool profile')


def validate_snapshot(before, after, target):
    if before['http'] != after['http']:
        raise ValueError('HTTP container or warmed entry worker identity changed during planned restart')
    if set(before['services']) != set(after['services']):
        raise ValueError('Owned service set changed during planned restart')
    for role, previous in before['services'].items():
        current = after['services'][role]
        if previous['container'] != current['container']:
            raise ValueError('Container replacement is outside the bounded restart probe')
        service_role = role.split(':', 1)[0]
        restarted = (previous['restarts'] != current['restarts']
            or previous.get('started_at') != current.get('started_at'))
        if service_role != target and service_role not in DEPENDENT_ROLES and restarted:
            raise ValueError('Unexpected service restart outside the planned outage dependencies')


def validate_database(probe):
    if (probe.get('database') != 'kt_e2e' or probe.get('user_exists') is not True
            or probe.get('max_connections') != 64
            or any(probe.get(key) != 'on' for key in ('fsync', 'synchronous_commit', 'full_page_writes'))):
        raise ValueError('Fresh database read or frozen durability/connection budget differs')


class ConnectionRecovery:
    def __init__(self, compose_path, fixture_path, output_path):
        self.compose_path, self.fixture_path, self.output_path = map(Path, (compose_path, fixture_path, output_path))
        self.config = json.loads(self.compose_path.read_text())
        validate_target(self.config, self.compose_path, self.fixture_path, self.output_path)
        if self.output_path.exists() or not self.output_path.parent.is_dir():
            raise ValueError('Recovery report requires a fresh path in an existing evidence directory')
        fixture = json.loads(self.fixture_path.read_text())
        if (fixture.get('isolated_demo') is not True or fixture.get('database') != 'kt_e2e'
                or fixture.get('source_ownership', {}).get('command') != 'seed_prepared_startup_load'
                or digest(fixture['fixture_spec']) != fixture['plan_sha']):
            raise ValueError('Recovery requires a frozen owned synthetic fixture')
        self.account = fixture['accounts'][0]
        if (not isinstance(self.account.get('user_id'), int) or self.account['user_id'] <= 0
                or not self.account.get('username', '').startswith('pp-' + fixture['run_id'] + '-')):
            raise ValueError('Synthetic account binding differs')
        self.client = None
        self.report = {'state': 'running', 'passed': False, 'project': PROJECT,
            'port': PORT, 'planned_restart_order': ['pgbouncer', 'db'], 'stages': [],
            'fixture_sha256': hashlib.sha256(self.fixture_path.read_bytes()).hexdigest(),
            'compose_sha256': hashlib.sha256(self.compose_path.read_bytes()).hexdigest(),
            'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'user_id': self.account['user_id'], 'browser_operability_measured': False,
            'scope': 'One initial authentication-session write, then authenticated HTTP entry reads and fresh SQL after planned isolated restarts; no pressure load or answer/draft/capture write-recovery claim.',
            'limitations': ['Dependent background management processes may restart during the explicitly planned outage; their restart deltas are recorded.',
                'HTTP entry worker identities and persistent client coverage are checked; background zero-restart recovery is not claimed.']}

    def call(self, arguments, *, timeout=30):
        result = subprocess.run(arguments, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('Recovery command failed; private operator logs retain the named stage')
        return result.stdout

    def containers(self):
        ids = self.call(['docker', 'ps', '-q', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT]).split()
        rows = json.loads(self.call(['docker', 'inspect', *ids])) if ids else []
        return [row for row in rows if row['Config'].get('Labels', {}).get('com.docker.compose.oneoff', '').lower() != 'true']

    def by_role(self, role):
        rows = [row for row in self.containers() if row['Config']['Labels']['com.docker.compose.service'] == role]
        if len(rows) != 1:
            raise ValueError('One owned ' + role + ' service required')
        return rows[0]

    def assert_idle(self):
        if any(row['Config']['Labels'].get('com.docker.compose.service') == 'load' for row in self.containers()):
            raise ValueError('Running pressure load forbids planned restarts')

    def preflight(self):
        self.assert_idle()
        self.wait_ready()
        actual = dict(value.split('=', 1) for value in self.by_role('entry')['Config']['Env'] if '=' in value)
        if actual.get('DATABASE_CONN_MAX_AGE') != '60' or actual.get('SCHOOL_POOL_PROFILE') != 'shared-entry-v2':
            raise ValueError('Actual HTTP entry profile is not reuse60')
        self.workers = int(actual['GUNICORN_WORKERS'])
        self.application_name = actual['DATABASE_APPLICATION_NAME']
        if self.workers not in (15, 19) or self.application_name != 'kt-school-entry':
            raise ValueError('Unexpected entry worker/application binding')

    def wait_ready(self):
        deadline = time.monotonic() + 90
        expected = {role: int(service.get('deploy', {}).get('replicas', 1))
            for role, service in self.config['services'].items() if not service.get('profiles') and role != 'migrate'}
        while time.monotonic() < deadline:
            rows = self.containers()
            counts = {role: sum(row['Config']['Labels'].get('com.docker.compose.service') == role for row in rows) for role in expected}
            if counts == expected and all(row['State']['Running'] and row['State'].get('Health', {}).get('Status', 'healthy') == 'healthy' for row in rows):
                return
            time.sleep(1)
        raise ValueError('Owned services did not recover within the fixed90 second bound')

    def login(self):
        self.client = HttpClient(f'http://127.0.0.1:{PORT}')
        deadline = time.monotonic() + 30
        checked(self.client, '/api/v1/auth/csrf/', deadline=deadline)
        context = checked(self.client, '/api/v1/auth/login/', {'username': self.account['username'],
            'password': self.account['password']}, deadline=deadline)
        if context.get('user', {}).get('id') != self.account['user_id']:
            raise ValueError('Authenticated synthetic user identity differs')

    def authenticated_read(self, client):
        began = time.monotonic()
        context = checked(client, '/api/v1/auth/session/', deadline=began + 10)
        if context.get('user', {}).get('id') != self.account['user_id']:
            raise ValueError('Original authenticated cookie did not recover its user')
        return (time.monotonic() - began) * 1000

    def pooled(self, command):
        pooler = self.by_role('pgbouncer')['Id']
        raw = self.call(['docker', 'exec', pooler, 'sh', '-c',
            'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -A -F "|" -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -c "$1"',
            'connection-recovery-probe', command])
        return list(csv.DictReader(raw.splitlines(), delimiter='|'))

    def pool_counts(self):
        clients = sum(row.get('database') == 'kt_entry' and row.get('application_name') == self.application_name
            for row in self.pooled('SHOW CLIENTS'))
        stats = next((row for row in self.pooled('SHOW STATS') if row.get('database') == 'kt_entry'), None)
        if stats is None:
            raise ValueError('HTTP entry pool statistics missing')
        return {'persistent_clients': clients, 'queries': int(stats['total_query_count'])}

    def database(self):
        user_id = int(self.account['user_id'])
        sql = "SELECT json_build_object('database',current_database(),'backend_pid',pg_backend_pid()," \
            "'postmaster_started_at',pg_postmaster_start_time(),'max_connections',current_setting('max_connections')::int," \
            "'fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit')," \
            "'full_page_writes',current_setting('full_page_writes'),'user_exists',EXISTS(SELECT 1 FROM accounts_user WHERE id=" + str(user_id) + '))'
        raw = self.call(['docker', 'exec', self.by_role('db')['Id'], 'sh', '-c',
            'exec psql -X -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kt_e2e -c "$1"',
            'connection-recovery-read-only', sql])
        probe = json.loads(raw)
        validate_database(probe)
        return probe

    def snapshot(self):
        rows = self.containers()
        services = {}
        http = {}
        for item in rows:
            role = item['Config']['Labels']['com.docker.compose.service']
            # Several background replicas are recorded individually.
            key = role if role in ('entry', 'web', 'capture', 'assessment', 'teacher-read', 'db', 'pgbouncer', 'nginx', 'scheduler') else role + ':' + item['Id']
            services[key] = {'container': item['Id'], 'restarts': item.get('RestartCount', 0),
                'started_at': item['State'].get('StartedAt')}
            if role in HTTP_ROLES:
                http[role] = {'container': item['Id'], 'started_at': item['State'].get('StartedAt')}
        script = "import json,pathlib; p=pathlib.Path('/proc'); ids=(p/'1/task/1/children').read_text().split(); print(json.dumps({pid:(p/pid/'stat').read_text().rsplit(')',1)[1].split()[19] for pid in ids}))"
        workers = json.loads(self.call(['docker', 'exec', self.by_role('entry')['Id'], 'python', '-c', script]))
        if len(workers) != self.workers:
            raise ValueError('Entry worker identity coverage differs')
        http['entry']['workers'] = workers
        return {'http': http, 'services': services}

    def exercise(self):
        counts_before = self.pool_counts()
        first_read = self.authenticated_read(self.client)
        reads = 1
        cookies = [copy.copy(cookie) for cookie in self.client.cookies]
        def read(_):
            client = HttpClient(f'http://127.0.0.1:{PORT}')
            for cookie in cookies:
                client.cookies.set_cookie(copy.copy(cookie))
            try:
                return self.authenticated_read(client)
            finally:
                client.close()
        for _ in range(4):
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                list(pool.map(read, range(2 * self.workers)))
            reads += 2 * self.workers
            counts_after = self.pool_counts()
            if counts_after['persistent_clients'] >= self.workers:
                break
        if counts_after['persistent_clients'] < self.workers or counts_after['queries'] <= counts_before['queries']:
            raise ValueError('All warmed entry connections or fresh HTTP SQL activity could not be evidenced')
        return {'authenticated': True, 'fresh_database_read': True, 'first_authenticated_read_ms': first_read,
            'http_read_count': reads, 'persistent_clients': counts_after['persistent_clients'],
            'entry_workers': self.workers, 'http_query_count_delta': counts_after['queries'] - counts_before['queries'],
            'database': self.database()}

    def restart(self, role):
        if role not in ('pgbouncer', 'db'):
            raise ValueError('Only the two planned isolated targets may restart')
        item = self.by_role(role)
        labels = item['Config']['Labels']
        if labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.service') != role:
            raise ValueError('Restart target ownership differs')
        self.call(['docker', 'restart', '--time', '30', item['Id']], timeout=60)

    def persist(self):
        write_once(self.output_path, self.report)

    def close(self):
        if self.client is not None:
            self.client.close()

    def run(self):
        try:
            self.preflight()
            self.login()
            baseline = self.exercise()
            before = self.snapshot()
            self.report['stages'].append({'stage': 'warmed_baseline', **baseline})
            for target in ('pgbouncer', 'db'):
                self.assert_idle()
                self.restart(target)
                self.wait_ready()
                after = self.snapshot()
                validate_snapshot(before, after, target)
                result = self.exercise()
                after = self.snapshot()
                validate_snapshot(before, after, target)
                if 'database' in result and 'database' in baseline:
                    changed = result['database']['postmaster_started_at'] != baseline['database']['postmaster_started_at']
                    if changed != (target == 'db'):
                        raise ValueError('PostgreSQL postmaster restart identity differs from the planned target')
                deltas = {key: after['services'][key]['restarts'] - previous['restarts']
                    for key, previous in before['services'].items() if after['services'][key]['restarts'] != previous['restarts']}
                self.report['stages'].append({'stage': 'after_' + target + '_restart', **result,
                    'restart_deltas': deltas, 'http_processes_preserved': True})
                baseline, before = result, after
            self.report.update(state='completed', passed=True)
            return self.report
        except BaseException as error:
            self.report.update(state='failed', passed=False, error_type=type(error).__name__,
                failure_reason=str(error) if isinstance(error, ValueError) else 'Bounded probe failed; no recovery acceptance.')
            raise
        finally:
            try:
                self.close()
            except BaseException as error:
                self.report.update(state='failed', passed=False, cleanup_error_type=type(error).__name__)
                raise
            finally:
                self.persist()


def verify_recovery(compose_path, fixture_path, output_path):
    return ConnectionRecovery(compose_path, fixture_path, output_path).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise SystemExit('Run only as root on the explicitly prepared isolated DUT')
    report = verify_recovery(args.compose, args.fixture, args.output)
    print(json.dumps({key: report[key] for key in ('state', 'passed', 'project')}))


if __name__ == '__main__':
    main()
