"""Windows external drivers for the single owned 50/180 capacity retest bridge.

Remote work is limited to the ktdata-owned bridge directory and a loopback SSH
forward. Exit one from a workload is measured evidence, never a capacity verdict.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import threading
import time
import urllib.request

import capacity_retest
import student_capacity_protocol_v3 as protocol

BRIDGE = '/home/ktdata/capacity-retest-bridge-20261016'
NODE = r'C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64\node.exe'
BROWSERS = r'C:\Users\31606\Downloads\ATOP-tooling\pw-fresh-browsers'
BASE_URL = 'http://127.0.0.1:18085'
CELL_PATTERN = r'[A-Za-z0-9_-]{20}'
HASH_PATTERN = r'[0-9a-f]{64}'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def private(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(raw)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.pending')
    private(temporary, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode())
    os.replace(temporary, path)


def validate_request(request, manifest):
    capacity_retest.validate_manifest(manifest)
    if (request.get('version') != 'student-capacity-driver-request-v1'
            or not re.fullmatch(CELL_PATTERN, str(request.get('cell_id', '')))
            or not re.fullmatch(CELL_PATTERN, str(request.get('run_id', '')))
            or request.get('mode') not in capacity_retest.MODES or request.get('count') not in (50, 180)
            or request.get('workload') != 'paper-20q'
            or request.get('workload_profile') != capacity_retest.WORKLOAD_PROFILE):
        raise ValueError('Only the owned bounded retest request is permitted')
    actors, browsers = request.get('actor_ids'), request.get('browser_account_ids')
    if (not isinstance(actors, list) or len(actors) != request['count']
            or any(not isinstance(a, str) or not a for a in actors) or len(set(actors)) != len(actors)
            or not isinstance(browsers, list) or len(browsers) not in (0, 2)
            or any(not isinstance(a, str) for a in browsers) or len(set(browsers)) != len(browsers)
            or set(browsers) - set(actors)):
        raise ValueError('Full roster and disjoint browser/protocol account split differ')
    cell = BRIDGE + '/' + request['cell_id']
    if (request.get('bridge_directory') != cell or request.get('fixture_file') != cell + '/fixture.json'
            or request.get('plan_file') != cell + '/plan.json'
            or request.get('phase_bounds') != capacity_retest.phase_bounds(request['workload'])
            or any(not re.fullmatch(HASH_PATTERN, str(request.get(k, ''))) for k in ('fixture_sha256', 'plan_id'))):
        raise ValueError('Bridge paths, hashes or phase budgets differ')
    return request


def validate_inputs(request, fixture_raw, plan_raw):
    if sha(fixture_raw) != request['fixture_sha256']:
        raise ValueError('Downloaded fixture SHA256 differs')
    fixture, plan = json.loads(fixture_raw.decode('utf-8-sig')), json.loads(plan_raw.decode('utf-8-sig'))
    if protocol.digest({k: v for k, v in plan.items() if k != 'plan_id'}) != plan.get('plan_id'):
        raise ValueError('Downloaded plan hash differs')
    rebuilt = protocol.build_plan(fixture, mode=request['mode'], count=request['count'], run_id=request['run_id'],
        fixture_sha256=request['fixture_sha256'], base_url=BASE_URL, workload=request['workload'],
        workload_profile=request['workload_profile'],
        actor_ids=request['actor_ids'], browser_account_ids=request['browser_account_ids'])
    if plan != rebuilt or plan['plan_id'] != request['plan_id']:
        raise ValueError('Frozen server plan and local driver source/roster differ')
    return fixture, plan


def release_document(request, ready_documents, *, monotonic, wall):
    expected = 2 if request['browser_account_ids'] else 1
    if len(ready_documents) != expected or any((r.get('ready') is not True and r.get('state') != 'ready')
            or r.get('plan_id') != request['plan_id'] or r.get('run_id') != request['run_id'] for r in ready_documents):
        raise ValueError('Every intended driver must confirm the same plan before common release')
    return {'release_monotonic': monotonic + 3, 'release_wall_time': wall + 3}


def driver_result(exit_codes, *, artifacts_complete):
    return 'driver_completed' if artifacts_complete and all(code in (0, 1) for code in exit_codes.values()) else 'driver_failed'


def classify_generator(samples, *, release_lateness_ms, minimum_memory_bytes=512 * 1024**2):
    issues = []
    if not samples or any(s.get('available_memory_bytes') is None or s.get('heartbeat_lateness_ms') is None for s in samples):
        issues.append('generator_resource_observations_missing')
    if any(s.get('available_memory_bytes') is not None and s['available_memory_bytes'] < minimum_memory_bytes for s in samples):
        issues.append('generator_available_memory_below_bound')
    if any((s.get('heartbeat_lateness_ms') or 0) > 1000 for s in samples):
        issues.append('generator_independent_heartbeat_over_1000ms')
    if release_lateness_ms is None or release_lateness_ms > 1000:
        issues.append('generator_release_missed')
    return {'generator_valid': not issues, 'issues': issues, 'release_lateness_ms': release_lateness_ms,
            'minimum_available_memory_bytes': minimum_memory_bytes,
            'classification_uses_server_backlog': False}


def driver_commands(request, checkout, manifest_path, cell):
    checkout, cell = Path(checkout), Path(cell)
    script = checkout / 'server/scripts/load/student_capacity_protocol_v3.py'
    common = ['--fixture', str(cell / 'fixture.json'), '--mode', request['mode'], '--count', str(request['count']),
        '--run-id', request['run_id'], '--workload', request['workload'], '--actor-ids', ','.join(request['actor_ids']),
        '--workload-profile', request['workload_profile'],
        '--browser-account-ids', ','.join(request['browser_account_ids']), '--base-url', BASE_URL,
        '--plan-output', str(cell / 'protocol-plan.json'), '--output', str(cell / 'records.jsonl'),
        '--summary', str(cell / 'protocol-summary.json'), '--ready-file', str(cell / 'protocol-ready.json'),
        '--release-file', str(cell / 'release.json')]
    result = {'protocol': [str(checkout / '.venv/Scripts/python.exe'), str(script), *common]}
    if request['browser_account_ids']:
        browser_common = ['--run-manifest', str(manifest_path), '--fixture', str(cell / 'fixture.json'),
            '--plan', str(cell / 'plan.json'), '--output', str(cell / 'browser'), '--base-url', BASE_URL,
            '--account-ids', ','.join(request['browser_account_ids'])]
        result['browser'] = [NODE, str(checkout / 'client/e2e/student-capacity-retest-browser.mjs'), *browser_common,
            '--ready-file', str(cell / 'browser-ready.json'), '--release-file', str(cell / 'release.json'),
            '--pressure-finished-file', str(cell / 'pressure-finished.json')]
        result['visual'] = [NODE, str(checkout / 'client/e2e/student-capacity-visual.mjs'), *browser_common,
            '--visuals-only', '--teacher-only']
    return result


class Remote:
    def __init__(self, host):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', host):
            raise ValueError('SSH host must be an explicit configured alias')
        self.host = host

    def call(self, command, *, timeout=30):
        return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', self.host, command],
            capture_output=True, check=True, timeout=timeout, creationflags=HIDDEN).stdout

    def identity(self):
        if self.call('id -un').strip() != b'ktdata':
            raise ValueError('The bridge must use the non-root ktdata SSH identity')
        result = self.call("stat -c '%U %a' -- " + shlex.quote(BRIDGE)).decode().strip()
        if result != 'ktdata 700':
            raise ValueError('The exact bridge directory must be ktdata-owned mode 700')
        if self.call('realpath -- ' + shlex.quote(BRIDGE)).decode().strip() != BRIDGE:
            raise ValueError('Bridge root cannot be a link to another experiment')

    def cell_identity(self, directory):
        self.bound(directory)
        if (self.call("stat -c '%U %a' -- " + shlex.quote(directory)).decode().strip() != 'ktdata 700'
                or self.call('realpath -- ' + shlex.quote(directory)).decode().strip() != directory):
            raise ValueError('Cell bridge directory must be owned mode 700 without links')

    def read(self, path, *, optional=False):
        self.bound(path)
        command = ('if test -f ' + shlex.quote(path) + '; then cat -- ' + shlex.quote(path) + '; fi') if optional else ('cat -- ' + shlex.quote(path))
        return self.call(command)

    def bound(self, path):
        if not (path.startswith(BRIDGE + '/') and '..' not in path.split('/')
                and re.fullmatch(r'[A-Za-z0-9_./-]+', path)):
            raise ValueError('Remote path escapes the dedicated bridge directory')

    def download(self, remote, local):
        self.bound(remote)
        if self.call("stat -c '%U %a' -- " + shlex.quote(remote)).decode().strip() != 'ktdata 600':
            raise ValueError('Every frozen server input must be ktdata-owned mode 600')
        raw = self.read(remote)
        private(local, raw)
        return raw

    def upload(self, local, relative, directory):
        destination = directory + '/' + relative
        self.bound(destination)
        self.call('mkdir -p -- ' + shlex.quote(destination.rsplit('/', 1)[0]))
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', str(local), self.host + ':' + destination],
            check=True, capture_output=True, timeout=120, creationflags=HIDDEN)
        self.call('chmod 600 -- ' + shlex.quote(destination))


def port_open():
    with socket.socket() as probe:
        probe.settimeout(.5)
        return probe.connect_ex(('127.0.0.1', 18085)) == 0


def target_fingerprint(base=BASE_URL):
    with urllib.request.urlopen(base + '/login/', timeout=20) as response:
        html = response.read(4 * 1024**2).decode('utf-8')
    assets = sorted(set(re.findall(r'(?:src|href)=[\"\'](/static/[^\"\']+\.(?:js|css))[\"\']', html)))
    if not assets:
        raise ValueError('Target login page does not expose verifiable built frontend assets')
    hashes = {}
    for path in assets:
        with urllib.request.urlopen(base + path, timeout=20) as response:
            hashes[path] = sha(response.read(32 * 1024**2))
    with urllib.request.urlopen(base + '/api/v1/auth/csrf/', timeout=20) as response:
        value = json.loads(response.read(1024 * 1024))
        if not value.get('data', value).get('csrf_token'):
            raise ValueError('Target fresh-CSRF endpoint is not available')
    return hashes


def ensure_forward(remote, log):
    owned = None
    if not port_open():
        owned = subprocess.Popen(['ssh', '-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ServerAliveInterval=30', '-L', '127.0.0.1:18085:127.0.0.1:18085', remote.host],
            stdout=log, stderr=log, creationflags=HIDDEN)
        deadline = time.monotonic() + 20
        while not port_open() and time.monotonic() < deadline and owned.poll() is None:
            time.sleep(.1)
        if not port_open():
            stop_owned(owned)
            raise RuntimeError('Owned SSH forward did not start')
    try:
        # The remote script executes only this read-only HTTP fingerprint helper.
        import inspect
        source = 'import urllib.request,re,json,hashlib\nBASE_URL=' + repr(BASE_URL) + '\ndef sha(raw): return hashlib.sha256(raw).hexdigest()\n'
        source += inspect.getsource(target_fingerprint) + '\nprint(json.dumps(target_fingerprint(),sort_keys=True))\n'
        expected = json.loads(remote.call('python3 -c ' + shlex.quote(source), timeout=90))
        if target_fingerprint() != expected:
            raise ValueError('Existing loopback listener differs from the isolated remote frontend')
    except Exception:
        if owned:
            stop_owned(owned)
        raise
    return owned


def stop_owned(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                       timeout=15, creationflags=HIDDEN, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


class GeneratorMonitor:
    def __init__(self, output):
        self.output, self.rows, self.stopped = output, [], threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        previous_cpu, latest, worst = None, time.monotonic(), 0
        try:
            if os.name != 'nt':
                raise RuntimeError('This external driver requires Windows resource measurement')
            class Memory(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    ('total', ctypes.c_ulonglong), ('available', ctypes.c_ulonglong),
                    ('total_page', ctypes.c_ulonglong), ('available_page', ctypes.c_ulonglong),
                    ('total_virtual', ctypes.c_ulonglong), ('available_virtual', ctypes.c_ulonglong),
                    ('extended', ctypes.c_ulonglong)]
            target = time.monotonic() + .1
            while not self.stopped.wait(max(0, target - time.monotonic())):
                now = time.monotonic()
                worst = max(worst, max(0, now - target) * 1000)
                # Never emit a catch-up timer burst after an independent stall.
                target = now + .1
                if now - latest < 1:
                    continue
                memory = Memory()
                memory.length = ctypes.sizeof(memory)
                if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                    raise OSError('GlobalMemoryStatusEx failed')
                idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
                if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                    raise OSError('GetSystemTimes failed')
                ticks = (idle.value, kernel.value + user.value)
                cpu = None
                if previous_cpu and ticks[1] > previous_cpu[1]:
                    cpu = 100 * (1 - (ticks[0] - previous_cpu[0]) / (ticks[1] - previous_cpu[1]))
                previous_cpu = ticks
                row = {'wall_time': time.time(), 'monotonic': now, 'cpu_percent': cpu,
                       'available_memory_bytes': memory.available, 'heartbeat_lateness_ms': worst}
                self.rows.append(row)
                self.output.write(json.dumps(row) + '\n')
                self.output.flush()
                latest, worst = now, 0
        except Exception as error:
            self.rows.append({'resource_error': type(error).__name__, 'available_memory_bytes': None,
                              'heartbeat_lateness_ms': None})
            self.output.write(json.dumps(self.rows[-1]) + '\n')
            self.output.flush()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=3)


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def stop_requested(remote, root, cell):
    if (root / 'STOP').exists() or (cell / 'STOP').exists():
        return True
    for name in ('controller.json', 'request.json'):
        raw = remote.read(BRIDGE + '/' + name, optional=True)
        if raw and json.loads(raw).get('state') == 'safety_stopped':
            return True
    return False


def process_request(request, manifest, checkout, manifest_path, remote):
    validate_request(request, manifest)
    root = Path(manifest['local_evidence_root']).resolve()
    cell = root / 'cells' / request['cell_id']
    if cell.exists():
        raise ValueError('Local cell evidence already exists; it must never be overwritten or rerun')
    cell.mkdir(parents=True)
    atomic_json(cell / 'request.json', request)
    processes, streams, exits, release, error = {}, [], {}, None, None
    monitor = None
    started = time.monotonic()
    try:
        remote.cell_identity(request['bridge_directory'])
        fixture_raw = remote.download(request['fixture_file'], cell / 'fixture.json')
        plan_raw = remote.download(request['plan_file'], cell / 'plan.json')
        _, plan = validate_inputs(request, fixture_raw, plan_raw)
        commands = driver_commands(request, checkout, manifest_path, cell)
        environment = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=BROWSERS)
        for name in ('protocol', 'browser'):
            if name not in commands:
                continue
            stream = os.fdopen(os.open(cell / (name + '.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
            streams.append(stream)
            processes[name] = subprocess.Popen(commands[name], cwd=checkout, env=environment,
                stdout=stream, stderr=stream, creationflags=HIDDEN)
        resource_stream = os.fdopen(os.open(cell / 'generator-resources.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        streams.append(resource_stream)
        monitor = GeneratorMonitor(resource_stream)
        monitor.start()
        ready_paths = [cell / (name + '-ready.json') for name in processes]
        deadline = started + request['phase_bounds']['preparation_seconds']
        next_controller_check = 0
        while not all(p.exists() for p in ready_paths):
            if any(p.poll() is not None for p in processes.values()):
                raise RuntimeError('Driver exited before the common readiness barrier')
            if time.monotonic() >= deadline:
                raise TimeoutError('Driver preparation exceeded the frozen 300-second bound')
            if (root / 'STOP').exists() or (cell / 'STOP').exists():
                raise RuntimeError('local_stop_requested')
            if time.monotonic() >= next_controller_check:
                if stop_requested(remote, root, cell):
                    raise RuntimeError('controller_safety_stopped')
                next_controller_check = time.monotonic() + 3
            time.sleep(.05)
        protocol_plan = load_json(cell / 'protocol-plan.json')
        if protocol_plan != plan:
            raise ValueError('Spawned protocol driver produced a different frozen plan')
        release = release_document(request, [load_json(p) for p in ready_paths], monotonic=time.monotonic(), wall=time.time())
        atomic_json(cell / 'release.json', release)
        remote.upload(cell / 'release.json', 'release.json', request['bridge_directory'])
        workload_deadline = (release['release_monotonic'] + request['phase_bounds']['login_window_seconds']
            + request['phase_bounds']['answer_seconds'] + request['phase_bounds']['acceptance_seconds']
            + request['phase_bounds']['processing_seconds'] + 60)
        while any(p.poll() is None for p in processes.values()):
            if processes['protocol'].poll() is not None and not (cell / 'pressure-finished.json').exists():
                atomic_json(cell / 'pressure-finished.json', {'plan_id': request['plan_id'], 'run_id': request['run_id'],
                    'protocol_exit_code': processes['protocol'].returncode, 'wall_time': time.time()})
            if time.monotonic() > workload_deadline:
                raise TimeoutError('Driver workload exceeded the frozen phase bounds')
            if (root / 'STOP').exists() or (cell / 'STOP').exists():
                raise RuntimeError('local_stop_requested')
            if time.monotonic() >= next_controller_check:
                if stop_requested(remote, root, cell):
                    raise RuntimeError('controller_safety_stopped')
                next_controller_check = time.monotonic() + 3
            time.sleep(.1)
        exits = {name: p.returncode for name, p in processes.items()}
        if 'visual' in commands:
            stream = os.fdopen(os.open(cell / 'visual.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
            streams.append(stream)
            visual = subprocess.Popen(commands['visual'], cwd=checkout, env=environment,
                stdout=stream, stderr=stream, creationflags=HIDDEN)
            processes['visual'] = visual
            visual_deadline = time.monotonic() + 300
            while visual.poll() is None:
                if time.monotonic() > visual_deadline:
                    raise TimeoutError('Post-pressure browser verification timed out')
                if time.monotonic() >= next_controller_check:
                    if stop_requested(remote, root, cell):
                        raise RuntimeError('controller_safety_stopped')
                    next_controller_check = time.monotonic() + 3
                time.sleep(.1)
            exits['visual'] = visual.returncode
    except Exception as exception:
        error = {'code': type(exception).__name__, 'message': str(exception)}
    finally:
        for name, process in processes.items():
            stop_owned(process)
            exits[name] = process.poll()
        if monitor:
            monitor.stop()
        for stream in streams:
            stream.close()
    records_path = cell / 'records.jsonl'
    browser_records = cell / 'browser/browser-records.jsonl'
    complete = records_path.exists() and (not request['browser_account_ids'] or browser_records.exists())
    records = []
    for path in (records_path, browser_records):
        if path.exists():
            try:
                records.extend(json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip())
            except (ValueError, OSError) as exception:
                error = {'code': 'record_decode_failed', 'message': str(exception)}
    if records and 'plan' in locals():
        try:
            atomic_json(cell / 'combined-summary.json', protocol.summarize(plan, records))
        except (ValueError, TypeError, KeyError) as exception:
            error = {'code': 'summary_invalid', 'message': str(exception)}
    skews = [r['release_skew_ms'] for r in records if isinstance(r.get('release_skew_ms'), (int, float))]
    resources = classify_generator(monitor.rows if monitor else [],
        release_lateness_ms=max(skews, default=None) if release else None)
    arrivals = [r['login_actual_monotonic_ms'] for r in records
                if isinstance(r.get('login_actual_monotonic_ms'), (int, float))]
    resources.update(login_window_planned_ms=10000, login_arrivals_observed=len(arrivals),
        login_arrivals_planned=request['count'],
        login_actual_release_window_ms=max(arrivals) - min(arrivals) if arrivals else None,
        release_lateness_definition='maximum actual personal dispatch minus its frozen planned click; excludes nominal ten-second spread')
    atomic_json(cell / 'generator-summary.json', resources)
    done = {'version': 'student-capacity-driver-result-v1', 'cell_id': request['cell_id'], 'run_id': request['run_id'],
        'plan_id': request['plan_id'], 'state': 'driver_failed' if error else driver_result(exits, artifacts_complete=complete),
        'files': {}, 'generator_valid': resources['generator_valid'], 'exit_codes': exits, 'error': error,
        'capacity_verdict': None}
    files = []
    for path in sorted(cell.rglob('*')):
        if path.is_file() and path.name not in ('done.json', 'done.json.pending'):
            relative = path.relative_to(cell).as_posix()
            done['files'][relative] = sha(path.read_bytes())
            files.append((path, relative))
    atomic_json(cell / 'done.json', done)
    for attempt in range(3):
        try:
            for path, relative in files:
                remote.upload(path, relative, request['bridge_directory'])
            remote.upload(cell / 'done.json', 'done.json.pending', request['bridge_directory'])
            remote.call('mv -- ' + shlex.quote(request['bridge_directory'] + '/done.json.pending') + ' ' + shlex.quote(request['bridge_directory'] + '/done.json'))
            break
        except (OSError, subprocess.SubprocessError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-manifest', required=True)
    parser.add_argument('--checkout', required=True)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--ssh-host', default='kt-school')
    args = parser.parse_args()
    manifest_path, checkout = Path(args.run_manifest).resolve(), Path(args.checkout).resolve()
    manifest = capacity_retest.validate_manifest(load_json(manifest_path))
    evidence = Path(manifest['local_evidence_root']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    remote = Remote(args.ssh_host)
    remote.identity()
    with os.fdopen(os.open(evidence / ('bridge-' + str(time.time_ns()) + '.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as log:
        owned = ensure_forward(remote, log)
        seen = set()
        try:
            while not (evidence / 'STOP').exists():
                control = remote.read(BRIDGE + '/controller.json', optional=True)
                if control and json.loads(control).get('state') in ('completed', 'completed_without_qualified_configuration',
                        'completed_with_incomplete_final', 'safety_stopped'):
                    return 0
                raw = remote.read(BRIDGE + '/request.json', optional=True)
                if raw:
                    document = json.loads(raw)
                    if document.get('state') == 'safety_stopped':
                        return 0
                    if document.get('state') == 'idle':
                        time.sleep(2)
                        continue
                    request = validate_request(document, manifest)
                    if request['cell_id'] not in seen:
                        done = process_request(request, manifest, checkout, manifest_path, remote)
                        seen.add(request['cell_id'])
                        print(json.dumps({k: done[k] for k in ('cell_id', 'state', 'generator_valid', 'exit_codes')}), flush=True)
                        if args.once:
                            return 0 if done['state'] == 'driver_completed' else 1
                time.sleep(2)
        finally:
            if owned:
                stop_owned(owned)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
