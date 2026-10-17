"""Continuously preserve existing isolated application logs across profile changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from capacity_retest import runtime_identity

ROOT, PROJECT, _ = runtime_identity()
ROLES = {'entry', 'assessment', 'capture', 'web', 'teacher-read', 'nginx',
    'scheduler', 'analytics-worker', 'session-submission-worker', 'pgbouncer'}
DIAGNOSTICS = ('SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED', 'SCHOOL_CAPTURE_DIAGNOSTICS_MODE', 'ASSESSMENT_REQUEST_TIMING_ENABLED')


def validate_paths(output, stop):
    if any(not Path(path).resolve().is_relative_to((ROOT / 'evidence').resolve()) for path in (output, stop)):
        raise ValueError('Log evidence and stop marker must stay inside the owned capacity evidence root')


def identity(item):
    labels = item['Config'].get('Labels', {})
    role = labels.get('com.docker.compose.service')
    if (labels.get('com.docker.compose.project') != PROJECT or role not in ROLES
            or labels.get('com.docker.compose.oneoff', '').lower() == 'true'):
        return None
    if not re.fullmatch('[a-f0-9]{64}', item['Id']):
        raise ValueError('Full Docker container identity required')
    environment = dict(value.split('=', 1) for value in item['Config'].get('Env', []) if '=' in value)
    return {'container_id': item['Id'], 'service': role, 'image_id': item['Image'],
        'started_at': item['State']['StartedAt'], 'running': item['State']['Running'],
        'restart_count': item.get('RestartCount', 0),
        'diagnostics': {key: environment.get(key) for key in DIAGNOSTICS}}


def stream_key(row):
    return row['container_id'] + ':' + row['started_at']


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


class Collector:
    def __init__(self, output, stop, duration):
        self.output, self.stop = Path(output), Path(stop)
        validate_paths(self.output, self.stop)
        self.output.mkdir(mode=0o700)
        self.duration = duration
        self.live = {}
        self.seen = set()
        self.rows = []
        self.report = {'project': PROJECT, 'state': 'collecting', 'started_at': time.time(),
            'freshness_clock': 'monotonic',
            'duration_bound_seconds': duration, 'poll_interval_seconds': 3, 'streams': self.rows,
            'coverage_gaps': [], 'lifecycle_confirmation_seconds': 10,
            'collector_sha256': file_hash(__file__),
            'scope': 'Owned current-lifetime Docker stdout/stderr; previous lifetimes remain in their original evidence.'}

    def discover(self, *, deadline=None):
        deadline = deadline if deadline is not None else time.monotonic() + 10
        retried = False
        def call(command, requested=5):
            nonlocal retried
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Log discovery budget exhausted')
                try:
                    return subprocess.run(command, text=True, capture_output=True, timeout=min(5, requested, remaining))
                except subprocess.TimeoutExpired:
                    self.report = getattr(self, 'report', {})
                    self.report.setdefault('discovery_timeouts', []).append({
                        'at': time.time(), 'command': command[:2], 'attempt': 2 if retried else 1})
                    if retried:
                        raise
                    retried = True
        command = ['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT]
        result = call(command)
        if result.returncode:
            raise RuntimeError('Owned project log discovery failed')
        ids = result.stdout.split()
        if not ids:
            raise RuntimeError('Owned project has no discoverable containers')
        result = call(['docker', 'inspect', *ids], requested=10)
        # A profile switch can remove one old identity during batch inspection.
        if not result.stdout.strip():
            raise RuntimeError('Owned project inspection unavailable')
        items = json.loads(result.stdout)
        found = {item['Id'] for item in items}
        if len(found) != len(items) or not found <= set(ids):
            raise RuntimeError('Owned project inspection identity mismatch')
        missing = set(ids) - found
        if missing:
            refreshed = call(command)
            if refreshed.returncode or missing.intersection(refreshed.stdout.split()):
                raise RuntimeError('Owned project inspection incomplete')
            self.report.setdefault('lifecycle_observations', []).append({
                'at': time.time(), 'removed_during_discovery': sorted(missing)})
        elif result.returncode:
            raise RuntimeError('Owned project inspection failed')
        rows = [row for item in items if (row := identity(item)) is not None]
        return rows

    def persist(self):
        temporary = self.output / 'manifest.tmp'
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.report, stream, indent=2)
        temporary.chmod(0o600)
        os.replace(temporary, self.output / 'manifest.json')

    def attach(self, row):
        key = stream_key(row)
        number = len(self.rows)
        filename = f'{number:04d}-{row["service"]}-{row["container_id"]}.log'
        path = self.output / filename
        stream = os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb')
        # A second lifetime of the same container starts at its new StartedAt.
        prior_lifetime = any(old['container_id'] == row['container_id'] for old in self.rows)
        command = ['docker', 'logs', '--follow', '--since', row['started_at']]
        command += [row['container_id']]
        try:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        except BaseException:
            stream.close()
            raise
        record = {**row, 'file': filename, 'state': 'following', 'attached_at': time.time(),
            'history': 'since_container_start' if not prior_lifetime else 'since_new_started_at'}
        self.rows.append(record)
        self.live[key] = (process, stream, record)
        self.seen.add(key)

    def close_stream(self, key, reason):
        process, stream, record = self.live.pop(key)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stream.close()
        path = self.output / record['file']
        record.update(state='closed', closed_reason=reason, closed_at=time.time(),
            exit_code=process.returncode, bytes=path.stat().st_size, sha256=file_hash(path))

    def run(self):
        deadline = float('inf') if self.duration is None else time.monotonic() + self.duration
        try:
            while time.monotonic() < deadline and not self.stop.exists():
                rows = self.discover()
                current = {stream_key(row): row for row in rows}
                if any(process.poll() is not None and current.get(key, {}).get('running')
                       for key, (process, _, _) in self.live.items()):
                    # Docker's stop visibility can lag the successful logs EOF.
                    # Observe a bounded transition; never restart the follower.
                    confirmation_deadline = time.monotonic() + 10
                    for attempt in range(20):
                        rows = self.discover(deadline=confirmation_deadline)
                        current = {stream_key(row): row for row in rows}
                        if not any(process.poll() is not None and current.get(key, {}).get('running')
                                for key, (process, _, _) in self.live.items()):
                            break
                        remaining = confirmation_deadline - time.monotonic()
                        if attempt == 19 or remaining <= 0:
                            break
                        time.sleep(min(0.5, remaining))
                for key in list(self.live):
                    process = self.live[key][0]
                    if process.poll() not in (None, 0):
                        self.report.setdefault('coverage_gaps', []).append({
                            'at': time.time(), 'stream_key': key, 'reason': 'failed_follow_exit'})
                        self.close_stream(key, 'failed_follow_exit')
                        raise RuntimeError('Log follower failed; diagnostic coverage incomplete')
                    if key not in current:
                        self.close_stream(key, 'container_removed_or_new_lifetime')
                    elif process.poll() is not None:
                        if current[key]['running']:
                            self.report.setdefault('coverage_gaps', []).append({
                                'at': time.time(), 'stream_key': key, 'reason': 'unexpected_follow_exit'})
                            self.close_stream(key, 'unexpected_follow_exit')
                            raise RuntimeError('Log follower exited while its owned container remained running')
                        self.close_stream(key, 'container_stopped')
                for row in rows:
                    key = stream_key(row)
                    if key not in self.seen:
                        self.attach(row)
                self.report['updated_at'] = time.time()
                self.report['updated_at_monotonic'] = time.monotonic()
                self.persist()
                # Short waits respond promptly to a coordinator stop marker.
                for _ in range(6):
                    if self.stop.exists() or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
            self.report['state'] = 'completed'
            self.report['stop_reason'] = 'coordinator_marker' if self.stop.exists() else 'duration_bound'
        except BaseException as error:
            self.report.update(state='failed', error_type=type(error).__name__)
            self.report.setdefault('coverage_gaps', []).append({'at': time.time(),
                'reason': 'collection_failed', 'error_type': type(error).__name__})
            raise
        finally:
            for key in list(self.live):
                self.close_stream(key, 'collector_stop')
            self.report['ended_at'] = time.time()
            self.persist()


def duration_bound(value):
    if value == 0 and PROJECT == 'kt-school-e2e-capacity-retest-20261016':
        return None
    if 1 <= value <= 43200:
        return value
    raise ValueError('Use1..43200seconds, or0 only for the owned coordinator-bound retest')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--stop-file', required=True)
    parser.add_argument('--duration', type=int, default=14400)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise SystemExit('Root on the explicitly owned DUT required')
    try:
        duration = duration_bound(args.duration)
    except ValueError as error:
        parser.error(str(error))
    Collector(args.output, args.stop_file, duration).run()


if __name__ == '__main__':
    main()
