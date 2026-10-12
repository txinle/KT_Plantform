#!/usr/bin/env python3
"""Long-lived stdin/stdout probe; each sample authenticates a fresh direct connection.

Only the root-private configuration path is passed in argv. SQL and driver error
messages (which may include credentials) never enter probe evidence.
"""
import argparse
import asyncio
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

IDENTITY_SQL = """SELECT current_database(), current_user,
    pg_get_userbyid(datdba), host(inet_server_addr()), inet_server_port(), 1
    FROM pg_database WHERE datname=current_database()"""
FAULT_CASES = {'connect_error', 'query_error', 'timeout', 'identity_error', 'os_error', 'heartbeat_lost'}


def private_config(path):
    path = Path(path)
    stat = path.stat()
    if path.is_symlink() or (os.name == 'posix' and (stat.st_uid != os.geteuid() or stat.st_mode & 0o077)):
        raise ValueError('Private probe configuration required')
    return json.loads(path.read_text(encoding='utf-8'))


def helper_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def expected_identity(config):
    return ('kt_e2e', 'kt_e2e_user', 'kt_e2e_user',
            config.get('expected_server_address'), config.get('expected_server_port'), 1)


async def probe(config, request_id, *, timeout=1):
    started = time.monotonic()
    row = {'source': 'native_authenticated_sql', 'request_id': request_id,
           'started_monotonic': started, 'deadline_seconds': timeout,
           'helper_sha256': helper_hash(), 'probe_secret_sha256': config_hash(config),
           'connect_seconds': None, 'query_seconds': None, 'outcome': 'identity_error'}
    expected = expected_identity(config)
    phase = 'connect'
    phase_started = started
    connection = None

    async def sample():
        import psycopg
        nonlocal connection, phase, phase_started
        stamp = time.monotonic()
        phase_started = stamp
        try:
            connection = await psycopg.AsyncConnection.connect(
                host=config['host'], port=config['port'], dbname=config['dbname'], user=config['user'],
                password=config['password'], autocommit=True, connect_timeout=1,
                application_name='school-native-safety-probe', options='-c default_transaction_read_only=on')
        finally:
            row['connect_seconds'] = time.monotonic() - stamp
        phase = 'query'
        stamp = time.monotonic()
        phase_started = stamp
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(IDENTITY_SQL)
                identity = await cursor.fetchone()
        finally:
            row['query_seconds'] = time.monotonic() - stamp
        row['outcome'] = 'ready' if identity == expected else 'identity_error'
        if row['outcome'] == 'ready':
            row['identity_sha256'] = config_hash(expected)

    try:
        if (config.get('dbname'), config.get('user')) != expected[:2] or not expected[3] or not expected[4] or not 0 < timeout <= 1:
            return row
        task = asyncio.create_task(sample())
        done, _ = await asyncio.wait({task}, timeout=max(0, started + timeout - time.monotonic()))
        if not done:
            row.update(outcome='timeout', timeout_phase=phase)
            row[phase + '_seconds'] = time.monotonic() - phase_started
            if connection is not None:
                connection.pgconn.finish()
                connection = None
            task.cancel()
            # psycopg may spend seconds cancelling a query. Closing our socket
            # above makes that cleanup harmless; it cannot extend this sample.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        else:
            task.result()
    except TimeoutError:
        row.update(outcome='timeout', timeout_phase=phase)
    except OSError as error:
        row.update(outcome='os_error', error_type=type(error).__name__)
    except Exception as error:
        row.update(outcome=phase + '_error', error_type=type(error).__name__)
    finally:
        if connection is not None:
            # libpq socket close is synchronous, including after async cancellation.
            connection.pgconn.finish()
        row['ended_monotonic'] = time.monotonic()
        row['total_seconds'] = row['ended_monotonic'] - started
        if row['outcome'] == 'ready' and row['total_seconds'] >= timeout:
            row.update(outcome='timeout', timeout_phase='total')
    return row


class NativeProbeClient:
    """One owned helper process; stale or missing replies fail closed."""
    def __init__(self, config):
        self.config = config
        self.process = None
        self.replies = queue.Queue()
        self.container_name = None
        self.reader = None

    def command(self):
        helper = str(Path(__file__).resolve())
        if not self.config.get('image'):
            return [self.config.get('python_executable', sys.executable), helper, '--config', self.config['secret_file']]
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', self.config['image']):
            raise ValueError('Immutable native helper image required')
        self.container_name = 'kt-school-e2e-native-probe-' + uuid.uuid4().hex[:16]
        return ['docker', 'run', '--rm', '-i', '--pull=never', '--user', '0:0', '--network', 'host', '--memory', '128m',
                '--cpu-shares', '64', '--label', 'com.docker.compose.project=kt-school-e2e',
                '--label', 'com.docker.compose.service=native-db-probe', '--name', self.container_name,
                '--label', 'com.docker.compose.oneoff=true',
                '--label', 'school.diagnostic.native-probe=' + self.container_name,
                '-v', self.config['secret_file'] + ':/probe-secret.json:ro',
                '-v', helper + ':/school_native_db_probe.py:ro', '--entrypoint', 'python',
                self.config['image'], '/school_native_db_probe.py', '--config', '/probe-secret.json']

    def _reader(self):
        process = self.process
        try:
            for line in process.stdout:
                row = json.loads(line)
                row['response_received_monotonic'] = time.monotonic()
                self.replies.put(row)
        except (OSError, ValueError):
            pass
        finally:
            self.replies.put({'outcome': 'heartbeat_lost'})

    def start(self):
        if self.process is not None:
            return
        self.process = subprocess.Popen(
            self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.reader = threading.Thread(target=self._reader, daemon=True)
        self.reader.start()
        try:
            hello = self.replies.get(timeout=3)
        except queue.Empty:
            raise OSError('Probe startup heartbeat missing') from None
        if hello.get('phase') != 'ready' or hello.get('helper_sha256') != helper_hash():
            raise OSError('Probe startup identity differs')

    def begin(self):
        try:
            self.start()
            request_id = uuid.uuid4().hex
            started = time.monotonic()
            if self.process.poll() is not None:
                return {'outcome': 'heartbeat_lost'}
            self.process.stdin.write(json.dumps({'request_id': request_id}) + '\n')
            self.process.stdin.flush()
            return {'request_id': request_id, 'requested_monotonic': started, 'deadline': started + 1.25}
        except (OSError, KeyError):
            return {'outcome': 'os_error'}

    def finish(self, ticket):
        if 'outcome' in ticket:
            return ticket
        try:
            row = self.replies.get(timeout=max(0, ticket['deadline'] - time.monotonic()))
        except queue.Empty:
            return {'outcome': 'heartbeat_lost'}
        if row.get('request_id') != ticket['request_id']:
            return {'outcome': 'heartbeat_lost'}
        row['requested_monotonic'] = ticket['requested_monotonic']
        row['ipc_seconds'] = row['response_received_monotonic'] - ticket['requested_monotonic']
        if not 0 <= row['ipc_seconds'] < 1.25:
            return {'outcome': 'heartbeat_lost', 'ipc_seconds': row['ipc_seconds']}
        return row

    def close(self):
        if self.process is None:
            return
        if self.container_name is not None:
            inspected = subprocess.run(['docker', 'inspect', self.container_name], capture_output=True,
                                       text=True, timeout=5, check=False)
            if inspected.returncode == 0:
                items = json.loads(inspected.stdout)
                labels = items[0].get('Config', {}).get('Labels', {}) if len(items) == 1 else {}
                if (labels.get('com.docker.compose.project') != 'kt-school-e2e'
                        or labels.get('com.docker.compose.service') != 'native-db-probe'
                        or labels.get('school.diagnostic.native-probe') != self.container_name
                        or items[0].get('Image') != self.config['image']):
                    raise ValueError('Refusing to stop an unowned native helper')
                subprocess.run(['docker', 'stop', '-t', '2', items[0]['Id']], capture_output=True,
                               timeout=5, check=True)
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()
        if self.reader is not None:
            self.reader.join(timeout=1)
        self.process = None
        self.replies = queue.Queue()


def calibrated_pairs(samples, binding):
    qualifying = []
    for row in samples:
        docker, native = row.get('postgres_probe', {}), row.get('native_postgres_probe', {})
        if (docker.get('source') == 'docker_exec_pg_isready' and docker.get('outcome') == 'timeout'
                and native.get('source') == 'native_authenticated_sql' and native.get('outcome') == 'ready'
                and native.get('probe_secret_sha256') == binding.get('probe_secret_sha256')
                and native.get('identity_sha256') == binding.get('native_identity_sha256')
                and native.get('helper_sha256') == binding.get('helper_sha256')
                and native.get('deadline_seconds') == 1 and 0 <= native.get('total_seconds', 1) < 1
                and 0 <= native.get('ipc_seconds', 1) < 1
                and max(docker.get('started_monotonic', float('inf')), native.get('started_monotonic', float('inf')))
                    < min(docker.get('ended_monotonic', 0), native.get('ended_monotonic', 0))):
            qualifying.append(native.get('request_id'))
    return len(set(identity for identity in qualifying if isinstance(identity, str) and len(identity) == 32))


def validate_calibration(certificate, binding):
    faults = certificate.get('fault_tests', {})
    return (certificate.get('binding') == binding and faults.get('passed') is True
            and faults.get('helper_sha256') == helper_hash()
            and FAULT_CASES <= set(faults.get('cases', []))
            and calibrated_pairs(certificate.get('samples', []), binding) >= 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    try:
        config = private_config(args.config)
        print(json.dumps({'phase': 'ready', 'helper_sha256': helper_hash()}), flush=True)
        loop = asyncio.new_event_loop()
        try:
            for line in sys.stdin:
                request = json.loads(line)
                row = loop.run_until_complete(probe(config, request['request_id']))
                print(json.dumps(row, separators=(',', ':')), flush=True)
        finally:
            loop.close()
    except Exception as error:
        print(json.dumps({'outcome': 'os_error', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
