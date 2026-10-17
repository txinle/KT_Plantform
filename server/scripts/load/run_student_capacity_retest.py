"""Remote coordinator for the explicitly owned external-driver 50/180 retest."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import time

import capacity_retest as contract
import run_student_capacity_matrix as previous
# The legacy coordinator prepends its directory; the frozen driver stays local.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import student_capacity_protocol_v3 as protocol


def driver_measurement_valid(done):
    capacity_bound = done.get('state') == 'driver_failed' and done.get('error') == {
        'code': 'TimeoutError', 'message': 'Driver workload exceeded the frozen phase bounds'}
    return done.get('generator_valid') is True and (
        (done.get('state') == 'driver_completed' and not done.get('error')) or capacity_bound)


def statistics_retry_needed(verification):
    return verification.get('stats_ready') is not True and verification.get('workflow_complete') is not False


def verification_target(items, sample, project, record_bytes):
    entries = [item for item in items if item['Config'].get('Labels', {}).get('com.docker.compose.project') == project
        and item['Config']['Labels'].get('com.docker.compose.service') == 'entry' and item['State']['Running']]
    resources = [row for row in sample.get('resource_rows', [])
        if row.get('scope') == 'isolated_test' and row.get('role') == 'entry']
    if len(entries) != 1 or not resources or any(not isinstance(row.get('working_set_bytes'), int) for row in resources):
        raise previous.SafetyStop('verifier_owned_memory_measurement_missing')
    limit = entries[0]['HostConfig'].get('Memory')
    working = sum(row['working_set_bytes'] for row in resources)
    required = max(1024**3, record_bytes * 8)
    available, total = sample.get('memory_available_bytes'), sample.get('memory_total_bytes')
    if (not isinstance(limit, int) or limit <= 0 or limit - working < required
            or not isinstance(available, int) or not isinstance(total, int)
            or available - required < max(2 * 1024**3, total * .15)):
        raise previous.SafetyStop('verifier_memory_headroom_insufficient')
    return {'container_id': entries[0]['Id'], 'role': 'entry', 'container_memory_limit_bytes': limit,
        'working_set_before_bytes': working, 'estimated_extra_memory_bytes': required,
        'host_available_before_bytes': available, 'record_bytes': record_bytes,
        'scope': 'post-pressure read-only verifier; existing role limits unchanged'}


def reusable_measurement(row):
    if (row.get('safety_stop') is not False
            or any(row.get(key) is not True for key in ('monitor_ok', 'generator_valid'))):
        return False
    if row.get('full_denominator_completed') is True and row.get('integrity_passed') is True:
        return True
    counts = row.get('counts', {})
    # A fully recorded capacity failure stays a failure and need not be replayed.
    return (counts.get('planned') == row.get('count') and isinstance(row.get('count'), int)
        and row['count'] > 0 and counts.get('missing') == counts.get('invalid') == 0
        and counts.get('completed', 0) + counts.get('failed', 0) == row['count'])


def load_result_resume(root, report_path, fixture_bytes, identity, project):
    """Reuse finalized cells with the same application, trajectory and configuration."""
    root, path = Path(root).resolve(), Path(report_path).resolve()
    if not path.is_relative_to(root / 'evidence') or path.name != 'matrix-report.json':
        raise ValueError('Resume report must be inside owned evidence')
    report = json.loads(path.read_text())
    if report.get('project') != project or report.get('state') == 'running':
        raise ValueError('Resume project differs or prior controller is running')
    prior_run = report.get('run_id', '')
    if not prior_run.isalnum():
        raise ValueError('Resume run identity invalid')
    rows, keys = [], set()
    for row in report.get('cells', []):
        row_identity = row.get('evidence_identity', {})
        if ({k:v for k,v in row_identity.items() if k != 'configuration'}
                != {k:v for k,v in identity.items() if k != 'configuration'} or not reusable_measurement(row)):
            continue
        candidate = row.get('candidate', '')
        if not candidate.isalnum():
            raise ValueError('Resume candidate invalid')
        directory = (root / row['evidence']).resolve()
        if not directory.is_relative_to(root / 'evidence'):
            raise ValueError('Resume cell escaped its original run')
        origin = directory.relative_to(root / 'evidence').parts
        source_run = origin[0].removeprefix('matrix-')
        if len(origin) != 3 or origin[1] != 'cells' or not origin[0].startswith('matrix-') or not source_run.isalnum():
            raise ValueError('Resume cell escaped its original run')
        config = json.loads((root / 'private' / f'compose-matrix-{source_run}-{candidate}.json').read_text())
        if config.get('name') != project or protocol.digest(config) != row_identity.get('configuration'):
            raise ValueError('Resume configuration differs')
        if json.loads((directory / 'cell-result.json').read_text()) != row:
            raise ValueError('Resume row differs from finalized cell result')
        if ((directory / 'fixture.json').read_bytes() != fixture_bytes
                or hashlib.sha256(fixture_bytes).hexdigest() != identity['baseline']):
            raise ValueError('Resume fixture differs')
        key = tuple(row.get(name) for name in ('candidate', 'stage', 'workload', 'count', 'mode', 'round'))
        if key in keys:
            raise ValueError('Duplicate resume result')
        keys.add(key)
        rows.append(row)
    return rows


def load_comparison_resume(root, report_path, fixture_bytes, config, project):
    """Reuse completed diagnostics only; failed generator cells remain invalid."""
    root, path = Path(root).resolve(), Path(report_path).resolve()
    if not path.is_relative_to(root / 'evidence') or path.name != 'matrix-report.json':
        raise ValueError('Resume report must be inside owned evidence')
    report = json.loads(path.read_text())
    if report.get('project') != project or report.get('state') == 'running':
        raise ValueError('Resume project differs or prior controller is running')
    prior_run = report.get('run_id', '')
    if not prior_run.isalnum() or json.loads((root / 'private' / f'compose-matrix-{prior_run}-R0.json').read_text()) != config:
        raise ValueError('Resume configuration differs')
    fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()
    driver_sha = hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest()
    expected_parameters, rows, keys = previous.parameters(config), [], set()
    for row in report.get('cells', []):
        if (row.get('stage') != 'comparison' or row.get('candidate') != 'R0'
                or row.get('mode') not in ('login-mixed', 'startup-mixed')
                or row.get('count') not in (50, 180) or row.get('workload') != 'comparison-2q'
                or row.get('safety_stop') is not False
                or any(row.get(key) is not True for key in
                       ('generator_valid', 'integrity_passed', 'stats_ready', 'full_denominator_completed'))):
            continue
        directory = (root / row['evidence']).resolve()
        if not directory.is_relative_to(path.parent / 'cells'):
            raise ValueError('Resume cell escaped its original report')
        if json.loads((directory / 'cell-result.json').read_text()) != row:
            raise ValueError('Resume row differs from preserved cell result')
        plan = json.loads((directory / 'plan.json').read_text())
        if ((directory / 'fixture.json').read_bytes() != fixture_bytes
                or plan.get('fixture_sha256') != fixture_sha):
            raise ValueError('Resume fixture differs')
        historical = plan.get('driver_sha256') != driver_sha
        if historical:
            frozen = root / 'ops/measurement-v3c1/student_capacity_protocol_v3.py'
            metadata = root / 'ops/measurement-v3c1/measurement-manifest.json'
            if not frozen.exists() or not metadata.exists():
                raise ValueError('Resume driver differs without preserved frozen measurement source')
            preserved = json.loads(metadata.read_text())
            if (hashlib.sha256(frozen.read_bytes()).hexdigest() != plan.get('driver_sha256')
                    or preserved.get('files', {}).get(frozen.name) != plan.get('driver_sha256')
                    or preserved.get('DUT_source_archive_sha256') != json.loads((root / 'private/run-manifest.json').read_text()).get('build_source_sha256')):
                raise ValueError('Resume historical driver/application binding differs')
        if (plan.get('version') != protocol.VERSION
                or any(plan.get(key) != row[key] for key in ('count', 'mode', 'workload'))
                or plan.get('plan_id') != protocol.digest({k: v for k, v in plan.items() if k != 'plan_id'})):
            raise ValueError('Resume driver or plan differs')
        source = json.loads((directory / 'source-config.json').read_text())
        if source.get('parameters') != expected_parameters or row.get('parameters') != expected_parameters:
            raise ValueError('Resume configuration parameters differ')
        if historical:
            observation = directory / 'observation.json'
            if not observation.exists() or json.loads(observation.read_text()).get('state') != 'completed':
                continue
            records = directory / 'merged-records.jsonl'
            if not records.exists():
                continue
            summary = protocol.summarize(plan, [json.loads(line) for line in records.read_text().splitlines() if line.strip()])
            row = {**row, 'timings': {key: summary.get(key) for key in ('login', 'startup', 'save', 'save_scheduled',
                    'pointer_scheduled', 'pointer_http', 'navigation', 'submission', 'processing', 'completion')},
                'performance_passed': summary['thresholds_met'], 'passed': False,
                'measurement_version': contract.MEASUREMENT_VERSION, 'metric_schema_version': contract.METRIC_SCHEMA_VERSION,
                'derived_from': {'report': str(path), 'plan_id': plan['plan_id'],
                    'source_driver_sha256': plan['driver_sha256'], 'records_sha256': hashlib.sha256(records.read_bytes()).hexdigest(),
                    'scope': 'Offline diagnostic reaggregation; original records and DB verification unchanged'}}
        key = row['count'], row['mode']
        if key in keys:
            raise ValueError('Duplicate resume comparison')
        keys.add(key)
        rows.append(row)
    return rows


class RetestSafetyWindow:
    def __init__(self, total_memory):
        self.floor = max(2 * 1024**3, math.ceil(total_memory * .15))
        self.last_memory_at = None
        self.low_samples = 0

    def sample(self, now, available, *, restarted=False, oom=False):
        if restarted:
            return 'unexpected_container_restart'
        if oom:
            return 'owned_container_oom'
        if available is None:
            return 'memory_monitor_missing'
        if self.last_memory_at is None or now - self.last_memory_at >= 15:
            self.last_memory_at = now
            self.low_samples = self.low_samples + 1 if available < self.floor else 0
        return 'available_memory_below_host_floor_three_15s_samples' if self.low_samples >= 3 else None


def measured_phase_cpu(records, ingress, segments, released_ms):
    """Join client phase calls to server request intervals using request IDs."""
    selected = {'login': set(), 'startup': set(), 'saving': set()}
    for record in records:
        if record.get('driver') == 'browser':
            continue
        bounds = {}
        role, login = record.get('role'), record.get('login_ms')
        login_started = record.get('login_actual_monotonic_ms', released_ms)
        login_ready = record.get('login_ready_monotonic_ms', login_started + (login or 0))
        if role in ('login', 'full-flow') and login is not None:
            bounds['login'] = login_started, login_ready
        startup = record.get('startup_ms')
        if role in ('startup', 'full-flow') and startup is not None and (role == 'startup' or login is not None):
            began = login_ready if role == 'full-flow' else released_ms
            bounds['startup'] = began, began + startup
        started = record.get('workload_start_monotonic_ms')
        acknowledgements = [sample['ack_monotonic_ms'] for sample in record.get('save_samples', [])
                            if sample.get('ack_monotonic_ms') is not None]
        if started is not None and acknowledgements:
            bounds['saving'] = started, max(acknowledgements)
        for call in record.get('http_calls', []):
            sent = call.get('http_send_monotonic_ms')
            if sent is None or not call.get('request_id'):
                continue
            for phase, (began, ended) in bounds.items():
                if began <= sent <= ended:
                    selected[phase].add(call['request_id'])
    result, windows = {}, {}
    for phase, identities in selected.items():
        requests = [row for row in ingress if row.get('request_id') in identities]
        if not requests:
            continue
        began = min(row['at'] - float(row['request_time']) for row in requests)
        ended = max(row['at'] for row in requests)
        weighted, covered = 0, 0
        for first, last, busy in segments:
            overlap = max(0, min(ended, last) - max(began, first))
            weighted += overlap * busy
            covered += overlap
        if covered:
            result[phase + '_cpu_busy'] = weighted / covered
            windows[phase] = {'server_started_at': began, 'server_ended_at': ended,
                             'covered_seconds': covered, 'matched_requests': len(requests)}
    return result | {'phase_cpu_windows': windows}


def bottleneck_dimension(row, excluded=()):
    if row.get('generator_valid') is not True or row.get('safety_stop'):
        return None
    details = row.get('diagnostics', {})
    busy = details.get('cpu_busy')
    def phase_cpu(phase):
        value = details.get(phase + '_cpu_busy')
        return value if value is not None else busy
    timings = row.get('timings', {})
    def exceeds(name, threshold):
        values = timings.get(name) or {}
        limits = contract.LIMITS.get(name, {'p95_ms': threshold})
        return (bool(values.get('planned') and values.get('missing'))
            or any(values.get(key) is not None and values[key] > limit for key, limit in limits.items()))
    choices = []
    if row.get('stats_ready') is False and busy is not None and busy < .85:
        choices.append('statistics')
    if exceeds('save_scheduled', 1000) or exceeds('navigation', 1000):
        saving_cpu = phase_cpu('saving')
        if (details.get('assessment_pool_waiting_max') or 0) > 0 and saving_cpu is not None and saving_cpu < .85:
            choices.append('assessment-pool')
        if (details.get('capture_pool_waiting_max') or 0) > 0 and saving_cpu is not None and saving_cpu < .85:
            choices.append('capture-pool')
        if (details.get('assessment_queue_p95_ms') or 0) > 100 and saving_cpu is not None and saving_cpu < .85:
            choices.append('assessment-workers')
        if (details.get('capture_queue_p95_ms') or 0) > 100 and saving_cpu is not None and saving_cpu < .85:
            choices += ['capture-workers', 'capture-pool']
    if exceeds('processing', 60000):
        choices.append('submission')
    if exceeds('login', 10000):
        login_cpu = phase_cpu('login')
        if login_cpu is not None and login_cpu >= .85:
            choices.append('login-reduce')
        elif details.get('login_rejected', 0):
            choices += ['login-limit', 'heavy-limit', 'entry-workers', 'entry-pool']
        elif details.get('entry_pool_waiting_max', 0) and login_cpu is not None and login_cpu < .85:
            choices.append('entry-pool')
    startup_cpu = phase_cpu('startup')
    if exceeds('startup', 5000) and startup_cpu is not None and startup_cpu < .85:
        if details.get('startup_rate_rejected', 0):
            choices += ['burst', 'rate']
        if details.get('startup_concurrency_rejected', 0):
            choices += ['startup-limit', 'heavy-limit', 'entry-workers', 'entry-pool']
        if (details.get('capture_pool_waiting_max') or 0) > 0:
            choices.append('capture-pool')
        if (details.get('capture_queue_p95_ms') or 0) > 100:
            choices.append('capture-workers')
    return next((name for name in choices if name not in excluded), None)


def quantile(values, probability=.95):
    values = sorted(value for value in values if isinstance(value, (int, float)) and math.isfinite(value))
    return values[max(0, math.ceil(len(values) * probability) - 1)] if values else None


def http_worker_inventory(items, config, proc=Path('/proc')):
    result = []
    for item in items:
        role = item['Config'].get('Labels', {}).get('com.docker.compose.service')
        if role not in contract.HTTP_WORKERS:
            continue
        expected = int(config['services'][role]['environment']['GUNICORN_WORKERS'])
        root = item['State']['Pid']
        def children(pid):
            path = proc / str(pid) / 'task' / str(pid) / 'children'
            return [int(value) for value in path.read_text().split()] if path.exists() else []
        def gunicorn(pid):
            path = proc / str(pid) / 'cmdline'
            return path.exists() and b'gunicorn' in path.read_bytes()
        master = root if gunicorn(root) else next((pid for pid in children(root) if gunicorn(pid)), None)
        workers = [pid for pid in children(master) if gunicorn(pid)] if master else []
        if len(workers) != expected:
            raise ValueError('Actual Gunicorn worker count differs from configured workers: ' + role)
        result.append({'role': role, 'container_id': item['Id'], 'master_pid': master,
                       'worker_pids': workers, 'workers': len(workers), 'configured_workers': expected})
    return result


def resource_headroom(config, changes, diagnostics):
    result = dict(changes)
    peaks = diagnostics.get('role_working_set_bytes', {})
    extra_memory = 0
    for role, key in contract.HTTP_WORKERS.items():
        if role not in config['services'] or key not in result or role not in peaks:
            continue
        service = config['services'][role]
        old = int(service['environment']['GUNICORN_WORKERS'])
        new = int(result[key])
        if new <= old:
            continue
        prediction = math.ceil(peaks[role] * new / old * 1.25)
        extra_memory += prediction - peaks[role]
        limit = service.get('mem_limit', 0)
        if isinstance(limit, int) and 0 < limit < prediction:
            result[f'resource.{role}.mem_limit'] = prediction
            if service.get('memswap_limit', 0) not in (0, -1) and service['memswap_limit'] < prediction:
                result[f'resource.{role}.memswap_limit'] = prediction
    for role, key in (('session-submission-worker', 'ASSESSMENT_SUBMISSION_WORKERS'),
                      ('analytics-worker', 'ASSESSMENT_ANALYTICS_WORKERS')):
        if role in config['services'] and key in result and role in peaks:
            old = int(config['services'][role].get('deploy', {}).get('replicas', 1))
            extra_memory += max(0, math.ceil(peaks[role] * (int(result[key]) / old - 1) * 1.25))
    available = diagnostics.get('memory_available_min_bytes')
    floor = max(2 * 1024**3, int(diagnostics.get('memory_total_bytes', 0) * .15))
    if extra_memory and available is not None and available - extra_memory < floor:
        raise ValueError('Observed host memory headroom cannot support this configuration step')
    return result


def collect_diagnostics(directory, collector_manifest, began, ended, *, records=()):
    observations = []
    sample_path = directory / 'observation.samples.jsonl'
    if sample_path.exists():
        observations = [json.loads(line) for line in sample_path.read_text().splitlines() if line.strip()]
    cpu, segments = [], []
    for first, last in zip(observations, observations[1:]):
        fraction = None
        a, b = first.get('host_cpu_counters') or {}, last.get('host_cpu_counters') or {}
        if isinstance(a, list) and isinstance(b, list):
            delta = [y - x for x, y in zip(a, b)]
            if sum(delta) > 0:
                fraction = 1 - sum(delta[3:5]) / sum(delta)
        elif isinstance(a, dict) and isinstance(b, dict):
            keys = ('user_ticks', 'nice_ticks', 'system_ticks', 'idle_ticks', 'iowait_ticks', 'irq_ticks', 'softirq_ticks', 'steal_ticks')
            total = sum(b.get(key, 0) - a.get(key, 0) for key in keys)
            if total > 0:
                fraction = 1 - sum(b.get(key, 0) - a.get(key, 0) for key in ('idle_ticks', 'iowait_ticks')) / total
        if fraction is not None:
            cpu.append(fraction)
            counter_times = [(row.get('measurement_status') or {}).get('host_cpu_counters', {}).get(
                'ended_at', row.get('sample_started_at', row.get('at'))) for row in (first, last)]
            if all(isinstance(at, (int, float)) for at in counter_times):
                segments.append((*counter_times, fraction))
    waits = []
    waits_by_alias = {}
    peaks = {}
    for sample in observations:
        pools = sample.get('pgbouncer') or {}
        for alias, row in pools.items() if isinstance(pools, dict) else []:
            if isinstance(row, dict):
                waits.append(int(row.get('cl_waiting', 0)))
                waits_by_alias[alias] = max(waits_by_alias.get(alias, 0), int(row.get('cl_waiting', 0)))
        by_role = {}
        for resource in sample.get('resource_rows', []):
            if resource.get('scope') == 'isolated_test':
                role = resource.get('role')
                by_role[role] = by_role.get(role, 0) + resource.get('working_set_bytes', 0)
        for role, value in by_role.items():
            peaks[role] = max(peaks.get(role, 0), value)
    queues = {'assessment': [], 'capture': [], 'entry': []}
    ingress = []
    layer_names = ('wsgi_application_seconds', 'connection_seconds', 'sql_seconds', 'commit_seconds',
                   'password_check_seconds', 'application_excluding_measured_database_seconds')
    streams = json.loads(collector_manifest.read_text()).get('streams', [])
    requests = {}
    parse_counts = {'lines': 0, 'json_candidates': 0, 'parsed': 0, 'failed': 0, 'missing_streams': 0}
    for stream in streams:
        path = collector_manifest.parent / stream['file']
        if not path.exists():
            parse_counts['missing_streams'] += 1
            continue
        with path.open(encoding='utf-8', errors='replace') as source:
            for line in source:
                parse_counts['lines'] += 1
                start = line.find('{')
                if start < 0:
                    continue
                parse_counts['json_candidates'] += 1
                try:
                    if '\ufffd' in line:
                        raise ValueError('Invalid UTF-8 diagnostic')
                    row = json.loads(line[start:])
                    if not isinstance(row, dict):
                        raise ValueError('Diagnostic object required')
                except ValueError:
                    parse_counts['failed'] += 1
                    continue
                parse_counts['parsed'] += 1
                at = row.get('at', row.get('wsgi_started_at'))
                if not isinstance(at, (int, float)) or not began <= at <= ended:
                    continue
                if stream['service'] == 'nginx':
                    ingress.append(row)
                else:
                    request_id = row.get('request_id')
                    delta = row.get('nginx_ingress_to_gunicorn_pre_wall_seconds')
                    if request_id:
                        request = requests.setdefault(request_id, {'request_id': request_id, 'role': stream['service']})
                        for key in layer_names:
                            value = row.get(key)
                            if isinstance(value, (int, float)):
                                request[key.removesuffix('_seconds') + '_ms'] = value * 1000
                        for key in ('sql_count', 'commit_count', 'connection_count'):
                            if isinstance(row.get(key), (int, float)):
                                request[key] = row[key]
                    if request_id and isinstance(delta, (int, float)) and not row.get('nginx_wall_correlation_untrusted'):
                        request['ingress_to_worker_ms'] = delta * 1000
                        if stream['service'] in queues:
                            queues[stream['service']].append(delta * 1000)
    previous.write_once(directory / 'request-layer-timings.json', list(requests.values()))
    release_path = directory / 'release.json'
    phases = measured_phase_cpu(records, ingress, segments,
        json.loads(release_path.read_text())['release_monotonic'] * 1000) if records and release_path.exists() else {}
    meminfo = Path('/proc/meminfo')
    total_memory = int(next(line.split()[1] for line in meminfo.read_text().splitlines()
                           if line.startswith('MemTotal:'))) * 1024 if meminfo.exists() else 0
    layer_metrics = {name.removesuffix('_seconds'): contract.metric_statistics([
        row.get(name.removesuffix('_seconds') + '_ms') for row in requests.values()], definition=name)
        for name in layer_names}
    layer_metrics['ingress_to_worker'] = contract.metric_statistics([row.get('ingress_to_worker_ms') for row in requests.values()])
    previous.write_once(directory / 'application-metrics.json', layer_metrics)
    status_counts = {}
    for row in ingress:
        key = str(row.get('status'))
        status_counts[key] = status_counts.get(key, 0) + 1
    wait_counts = {}
    for sample in observations:
        for row in sample.get('database_waits') or []:
            if row.get('scope') == 'isolated_test':
                key = str(row.get('wait_event_type')) + ':' + str(row.get('wait_event'))
                wait_counts[key] = wait_counts.get(key, 0) + row.get('connections', 1)
    return {**phases, 'cpu_busy': sum(cpu) / len(cpu) if cpu else None,
            'diagnostic_parse_counts': parse_counts,
            'diagnostic_parse_complete': not parse_counts['failed'] and not parse_counts['missing_streams'],
            'application_metrics': layer_metrics, 'ingress_status_counts': status_counts,
            'request_correlation_coverage': len(set(requests).intersection(row.get('request_id') for row in ingress)) / len(ingress) if ingress else None,
            'database_wait_sample_counts': wait_counts,
            'wal_first': next((row.get('database_wal') for row in observations if row.get('database_wal')), None),
            'wal_last': next((row.get('database_wal') for row in reversed(observations) if row.get('database_wal')), None),
            'pool_waiting_max': max(waits, default=None),
            'pool_waiting_by_alias': waits_by_alias,
            **{role + '_pool_waiting_max': waits_by_alias.get(alias)
               for role, alias in (('entry', 'kt_entry'), ('assessment', 'kt_assessment'), ('capture', 'kt_capture'))},
            **{role + '_queue_p95_ms': quantile(values) for role, values in queues.items()},
            'login_rejected': sum(row.get('operation') == 'login' and row.get('status') == 429 for row in ingress),
            'startup_rate_rejected': sum(row.get('operation') == 'start' and row.get('ingress_rate') == 'REJECTED' for row in ingress),
            'startup_concurrency_rejected': sum(row.get('operation') in ('start', 'resume', 'admission_status')
                and row.get('ingress_conn') == 'REJECTED' for row in ingress),
            'request_correlated_count': len(requests), 'sample_count': len(observations),
            'role_working_set_bytes': peaks,
            'memory_total_bytes': total_memory,
            'memory_available_min_bytes': min((row['memory_available_bytes'] for row in observations
                                               if row.get('memory_available_bytes') is not None), default=None)}


class RetestMatrix(previous.Matrix):
    def __init__(self, run_id, manifest, resume_comparisons=None, resume_results=None):
        import pwd
        self.manifest = contract.validate_manifest(manifest)
        self.root = Path(manifest['root'])
        previous.ROOT, previous.PROJECT, previous.PORT = self.root, manifest['project'], manifest['port']
        self.bridge = Path('/home/ktdata/capacity-retest-bridge-20261016')
        self.bridge.mkdir(mode=0o700, exist_ok=True)
        user = pwd.getpwnam('ktdata')
        self.bridge_owner = (user.pw_uid, user.pw_gid)
        os.chown(self.bridge, *self.bridge_owner)
        diagnostic_manifest = self.root / 'evidence' / ('matrix-' + run_id) / 'logs/manifest.json'
        super().__init__(run_id, diagnostic_manifest)
        self.report.update(same_DUT_generator=False, browser_operability_measured=False,
            workload_profile=contract.WORKLOAD_PROFILE, formal_required_cells=4,
            application_source_sha256=manifest['build_source_sha256'],
            measurement_version=contract.MEASUREMENT_VERSION,
            measurement_archive_sha256=manifest.get('measurement_archive_sha256'),
            scope='50/180 external protocol retest; actual browser observations recorded separately when executed',
            workload_bound_seconds=contract.phase_bounds('paper-20q'), phase='initializing')
        self.fixture_path = self.root / 'private/baseline-fixture.json'
        self.fixture_bytes = self.fixture_path.read_bytes()
        self.fixture = json.loads(self.fixture_bytes)
        self.resume_comparisons = resume_comparisons
        self.resume_results = resume_results
        self.comparison_reuse = {}
        self.collector = None
        self.publish_controller('running')
        self.publish_json(self.bridge / 'request.json', {'state': 'idle', 'run_id': self.run_id})

    def publish_json(self, path, value):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.chmod(0o600)
        os.chown(temporary, *self.bridge_owner)
        os.replace(temporary, path)

    def publish_controller(self, state):
        self.publish_json(self.bridge / 'controller.json', {'state': state, 'project': self.manifest['project'],
            'run_id': self.run_id, 'at': time.time(), 'active_cell': self.report.get('active_cell')})

    def evidence_identity(self, name):
        return {'application': self.manifest['build_source_sha256'],
                'configuration': protocol.digest(self.configs[name][1]),
                'baseline': hashlib.sha256(self.fixture_bytes).hexdigest(),
                'measurement': contract.MEASUREMENT_VERSION, 'metric_schema': contract.METRIC_SCHEMA_VERSION,
                'workload_profile': contract.WORKLOAD_PROFILE,
                'measurement_archive_sha256': self.manifest.get('measurement_archive_sha256'),
                'driver_sha256': hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest()}

    def start_collector(self, directory):
        self.diagnostic_manifest = directory / 'logs/manifest.json'
        self.collector_stop = directory / 'collector.stop'
        self.collector_log = (directory / 'collector.log').open('x')
        self.collector = subprocess.Popen([sys.executable, str(Path(__file__).with_name('collect_student_capacity_logs.py')),
            '--output', str(self.diagnostic_manifest.parent), '--stop-file', str(self.collector_stop), '--duration', '0'],
            stdout=self.collector_log, stderr=subprocess.STDOUT)

    def stop_collector(self):
        if getattr(self, 'collector', None) is None:
            return
        self.collector_stop.touch()
        try:
            self.collector.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.collector.terminate()
            self.collector.wait(timeout=5)
        finally:
            self.collector_log.close()
        collector, self.collector = self.collector, None
        if self.diagnostic_manifest.exists():
            manifest = json.loads(self.diagnostic_manifest.read_text())
            if collector.returncode != 0 or manifest.get('state') != 'completed' or manifest.get('coverage_gaps') != []:
                raise previous.SafetyStop('diagnostic_collection_finalization_failed')

    def save_report(self):
        super().save_report()
        fields = ('candidate', 'stage', 'count', 'mode', 'round', 'run_id', 'driver', 'account_id', 'metric',
                  'planned', 'observed', 'failed', 'missing', 'within_sla', 'p50_ms', 'p90_ms', 'p95_ms',
                  'p99_ms', 'max_ms', 'value_ms', 'unit', 'validity', 'definition')
        path = self.directory / 'metrics.csv'
        with path.with_suffix('.tmp').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fields, extrasaction='ignore')
            writer.writeheader()
            for row in self.results:
                for metric, values in row.get('timings', {}).items():
                    if values:
                        writer.writerow({**row, **values, 'metric': metric, 'driver': 'protocol'})
                for browser in row.get('browser_observations', []):
                    for metric, value in browser.get('metrics', {}).items():
                        writer.writerow({**row, 'metric': metric, 'driver': browser['driver'],
                            'account_id': browser['account_id'], **value})
        os.replace(path.with_suffix('.tmp'), path)
        path.chmod(0o600)
        columns = ('candidate', 'parent_candidate', 'dimension', 'count', 'mode', 'metric', 'percentile',
                   'before_ms', 'after_ms', 'limit_ms', 'change_fraction', 'decision')
        path = self.directory / 'candidate-comparison.csv'
        with path.with_suffix('.tmp').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, columns, extrasaction='ignore')
            writer.writeheader()
            for selection in self.selections:
                for metric in selection.get('metrics', []):
                    writer.writerow({**selection, **metric})
        os.replace(path.with_suffix('.tmp'), path)
        path.chmod(0o600)

    def add_observed_metrics(self, result, records, directory, verification):
        browser_rows = []
        for record in records:
            if record.get('driver') != 'browser':
                continue
            metrics = {}
            def one(name, value, limit=None):
                valid = isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
                metrics[name] = {'planned': 1, 'observed': int(valid), 'missing': int(not valid),
                    'value_ms': value if valid else None, 'unit': 'ms',
                    'within_sla': int(value <= limit) if valid and limit is not None else None,
                    'validity': 'complete' if valid else 'missing'}
            for name in ('login', 'startup', 'submission', 'processing', 'completion'):
                if record.get(name + '_ms') is not None:
                    one(name, record[name + '_ms'], contract.LIMITS.get(name, {}).get('p99_ms',
                        contract.LIMITS.get(name, {}).get('max_ms')))
            for index, sample in enumerate(record.get('navigation', [])):
                one('navigation:' + str(index), sample.get('duration_ms'), 3000)
                metrics['navigation:' + str(index)]['definition'] = sample.get('phase')
                one('navigation_button_wait:' + str(index), sample.get('button_wait_ms'))
            for index, sample in enumerate(record.get('save_samples', [])):
                one('raw_event_save:' + str(index), sample.get('event_scheduled_ms'), 3000)
                one('answer_pointer:' + str(index), sample.get('scheduled_ms'))
            browser_rows.append({'account_id': record['account_id'], 'driver': record.get('viewport_name',
                'desktop' if record.get('viewport', {}).get('width') == 1440 else 'mobile_emulation'),
                'status': record.get('status'), 'metrics': metrics})
        result['browser_observations'] = browser_rows
        statistics = []
        protocol_ids = {record['account_id'] for record in records if record.get('driver') != 'browser'}
        def timestamp(value):
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else None
        for line in (directory / 'database-evidence.jsonl').read_text().splitlines():
            row = json.loads(line)
            stat = row.get('statistics')
            if not stat:
                continue
            began, finished = timestamp(stat.get('ready_after')), timestamp(stat.get('finished_at'))
            delay = (finished - began) * 1000 if began is not None and finished is not None else None
            statistics.append({'account_id': row['account_id'], 'driver': row.get('driver', 'protocol'),
                'ready_after': stat.get('ready_after'), 'materialized_at': stat.get('finished_at'),
                'materialization_delay_ms': delay, 'source_revision': stat.get('source_revision'),
                'projection_version': stat.get('projection_version'), 'export_readable': stat.get('export_readable'),
                'first_readable_observed_at': time.time() if stat.get('export_readable') else None,
                'first_readable_observation_is_upper_bound': True})
        result['timings']['statistics'] = contract.metric_statistics([
            row['materialization_delay_ms'] for row in statistics if row['account_id'] in protocol_ids], limit=300000,
            definition='terminal and required materials ready to latest materialization; export independently checked')
        previous.write_once(directory / 'statistics-timings.json', statistics)
        result['statistics_applicable'] = verification.get('stats', {}).get('eligible_count', 0) > 0
        generator = json.loads((directory / 'generator-summary.json').read_text()) if (directory / 'generator-summary.json').exists() else {}
        result['generator'] = generator
        samples = [sample for record in records if record.get('driver') != 'browser' for sample in record.get('save_samples', [])]
        started = [sample.get('planned_monotonic_ms') for sample in samples if isinstance(sample.get('planned_monotonic_ms'), (int, float))]
        backlog = []
        if started:
            origin = min(started)
            for offset in range(0, 611, 10):
                at = origin + offset * 1000
                planned = [sample for sample in samples if isinstance(sample.get('planned_monotonic_ms'), (int, float)) and sample['planned_monotonic_ms'] <= at]
                pending = [sample for sample in planned if sample.get('event_ack_monotonic_ms') is None or sample['event_ack_monotonic_ms'] > at]
                backlog.append({'offset_seconds': offset, 'planned': len(planned),
                    'emitted': sum(sample.get('actual_event_monotonic_ms') is not None and sample['actual_event_monotonic_ms'] <= at for sample in planned),
                    'sent': sum(sample.get('capture_http_send_monotonic_ms') is not None and sample['capture_http_send_monotonic_ms'] <= at for sample in planned),
                    'acknowledged': len(planned) - len(pending), 'unconfirmed': len(pending),
                    'oldest_unconfirmed_ms': max((at - sample['planned_monotonic_ms'] for sample in pending), default=0)})
        previous.write_once(directory / 'client-backlog.json', backlog)
        result['client_backlog'] = {'peak_unconfirmed': max((row['unconfirmed'] for row in backlog), default=0),
            'end_window_unconfirmed': backlog[-1]['unconfirmed'] if backlog else None,
            'missing_event_acknowledgements': sum(sample.get('event_ack_monotonic_ms') is None for sample in samples)}
    def verify_with_monitor(self, web, arguments, log_path, *, timeout, check):
        command = ['docker', 'exec', '-e', 'POSTGRES_HOST=db', '-e', 'POSTGRES_POOL_ALIAS=',
            '-e', 'DATABASE_CONN_MAX_AGE=0', web, 'timeout', '--kill-after=5', str(timeout),
            'python', 'manage.py', *arguments]
        with log_path.open('x') as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    check()
                    time.sleep(1)
                if process.returncode not in (0, 1):
                    raise previous.SafetyStop('independent_verification_failed_or_budget_exhausted')
            finally:
                if process.poll() is None:
                    # The container timeout also bounds the read-only verifier after a monitor abort.
                    process.terminate()
                    process.wait(timeout=5)

    def preserve_finalization_failure(self, directory, cell, identity, error, primary_error):
        if primary_error is None:
            self.preserve_interrupted_cell(directory, cell, protocol, identity, error)
            return
        previous.write_once(directory / 'finalization-failure.json', {
            **cell, 'run_id': identity, 'state': 'interrupted_finalization',
            'passed': False, 'safety_stop': True, 'monitor_ok': False,
            'error_type': type(error).__name__,
            'failure_reason': str(error) if isinstance(error, previous.SafetyStop) else 'Controller exception',
            'primary_error_type': type(primary_error).__name__,
            'primary_failure_reason': str(primary_error) if isinstance(primary_error, previous.SafetyStop) else 'Controller exception'})

    def execute(self, cell):
        from student_capacity_retest_store import SnapshotStore
        if cell['count'] not in (50, 180):
            raise ValueError('Only 50/180 pressure permitted')
        path, config = self.configs[cell['candidate']]
        self.report['phase'] = 'restoring_baseline'
        self.save_report()
        self.stop_collector()
        SnapshotStore(self.manifest, path).restore(path)
        self.current = cell['candidate']
        identity = hashlib.sha256(json.dumps([self.run_id, cell], sort_keys=True).encode()).hexdigest()[:20]
        directory = self.directory / 'cells' / identity
        directory.mkdir(mode=0o700)
        self.start_collector(directory)
        self.wait_for_diagnostic_coverage(self.inspect_runtime())
        previous.write_once(directory / 'actual-http-workers.json', http_worker_inventory(self.inspect_runtime(), config))
        bridge = self.bridge / identity
        bridge.mkdir(mode=0o700)
        os.chown(bridge, *self.bridge_owner)
        actor_ids = [account['account_id'] for account in self.fixture['accounts'][:cell['count']]]
        browser_ids = [actor_ids[0], actor_ids[-1]] if cell.get('browser_count', 0) == 2 else []
        plan = protocol.build_plan(self.fixture, mode=cell['mode'], count=cell['count'], run_id=identity,
            fixture_sha256=hashlib.sha256(self.fixture_bytes).hexdigest(), base_url=f'http://127.0.0.1:{self.manifest["port"]}',
            workload=cell['workload'], actor_ids=actor_ids, browser_account_ids=browser_ids)
        (directory / 'fixture.json').write_bytes(self.fixture_bytes)
        (directory / 'fixture.json').chmod(0o600)
        previous.write_once(directory / 'plan.json', plan)
        for name in ('fixture.json', 'plan.json'):
            shutil.copy2(directory / name, bridge / name)
            os.chown(bridge / name, *self.bridge_owner)
        previous.write_once(directory / 'source-config.json', {'pool_profile': 'shared-entry-v3',
            'postgres_profile': 'isolated-pg17-v1', 'parameters': previous.parameters(config),
            'compose_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'protocol_version': protocol.VERSION})
        bounds = contract.phase_bounds(cell['workload'])
        observation, stop_file = directory / 'observation.json', directory / 'observer.stop'
        observer_log = (directory / 'observer.log').open('x')
        observer = subprocess.Popen([sys.executable, str(Path(__file__).with_name('observe_school_runtime.py')),
            '--project', self.manifest['project'], '--duration', str(bounds['observer_seconds']), '--interval', '2',
            '--scope', 'formal', '--abort-memory-below-mb', '0', '--stop-file', str(stop_file), '--output', str(observation)],
            stdout=observer_log, stderr=subprocess.STDOUT)
        started = time.time()
        error = None
        request = {'version': 'student-capacity-driver-request-v1', 'state': 'awaiting_driver',
            'workload_profile': contract.WORKLOAD_PROFILE,
            'cell_id': identity, 'run_id': identity, 'mode': cell['mode'], 'count': cell['count'], 'workload': cell['workload'],
            'actor_ids': actor_ids, 'browser_account_ids': browser_ids, 'fixture_sha256': plan['fixture_sha256'],
            'plan_id': plan['plan_id'], 'fixture_file': str(bridge / 'fixture.json'), 'plan_file': str(bridge / 'plan.json'),
            'bridge_directory': str(bridge), 'phase_bounds': bounds, 'target_origin': plan['target_origin']}
        try:
            deadline = time.monotonic() + 35
            while True:
                progress_path = observation.with_suffix('.progress.json')
                if progress_path.exists():
                    progress = json.loads(progress_path.read_text())
                    if (progress.get('latest_sample') or {}).get('state') == 'sampled':
                        break
                if observer.poll() is not None or time.monotonic() >= deadline:
                    raise previous.SafetyStop('observer_initial_sample_unavailable')
                time.sleep(.25)
            baseline = self.signature(self.inspect_runtime())
            meminfo = Path('/proc/meminfo').read_text()
            total = int(next(line.split()[1] for line in meminfo.splitlines() if line.startswith('MemTotal:'))) * 1024
            guard = RetestSafetyWindow(total)
            inspected_at = self.check_monitor(observer, observation, guard, baseline, 0)
            self.publish_json(self.bridge / 'request.json', request)
            self.report.update(phase='external_load', active_cell={**cell, 'cell_id': identity, 'started_at': started})
            self.save_report()
            self.publish_controller('running')
            deadline = time.monotonic() + sum(bounds[key] for key in ('preparation_seconds', 'answer_seconds',
                'acceptance_seconds', 'processing_seconds', 'login_window_seconds'))
            while not (bridge / 'done.json').exists():
                inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
                if time.monotonic() >= deadline:
                    raise previous.SafetyStop('external_driver_bound_exceeded')
                time.sleep(1)
            done = json.loads((bridge / 'done.json').read_text())
            previous.write_once(directory / 'driver-completion.json', done)
            for name, digest in done.get('files', {}).items():
                relative = PurePosixPath(name)
                if relative.is_absolute() or '..' in relative.parts or not relative.parts or not isinstance(digest, str):
                    raise previous.SafetyStop('external_driver_file_identity_invalid')
                source = bridge / name
                if hashlib.file_digest(source.open('rb'), 'sha256').hexdigest() != digest:
                    raise previous.SafetyStop('external_driver_artifact_digest_changed')
                (directory / name).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copy2(source, directory / name)
                (directory / name).chmod(0o600)
            records = []
            for name in ('records.jsonl', 'browser/browser-records.jsonl'):
                if (directory / name).exists():
                    records.extend(json.loads(line) for line in (directory / name).read_text().splitlines() if line.strip())
            with (directory / 'merged-records.jsonl').open('x', encoding='utf-8') as stream:
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
            (directory / 'merged-records.jsonl').chmod(0o600)
            summary = protocol.summarize(plan, records)
            previous.write_once(directory / 'recomputed-summary.json', summary)
            sample = json.loads(observation.with_suffix('.progress.json').read_text()).get('latest_sample') or {}
            target = verification_target(self.inspect_runtime(), sample, self.manifest['project'],
                (directory / 'merged-records.jsonl').stat().st_size)
            previous.write_once(directory / 'verification-runtime.json', target)
            verifier_id = target['container_id']
            internal = '/tmp/capacity-retest-' + identity
            self.call(['docker', 'exec', verifier_id, 'mkdir', '-p', internal])
            for name in ('fixture.json', 'plan.json', 'merged-records.jsonl'):
                self.call(['docker', 'cp', str(directory / name), verifier_id + ':' + internal + '/' + name])
            self.report.update(phase='independent_verification')
            self.save_report()
            stats_began = time.monotonic()
            verification = {}
            attempt = 0
            def verify_monitor():
                nonlocal inspected_at
                inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
            while time.monotonic() - stats_began < bounds['statistics_seconds']:
                inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
                filename = f'verification-{attempt:03d}.json'
                arguments = ['verify_student_capacity_load', '--fixture', internal + '/fixture.json',
                    '--plan', internal + '/plan.json', '--records', internal + '/merged-records.jsonl',
                    '--output', internal + '/' + filename]
                evidence_name = f'database-evidence-{attempt:03d}.jsonl'
                arguments += ['--evidence-output', internal + '/' + evidence_name]
                remaining = max(1, math.floor(bounds['statistics_seconds'] - (time.monotonic() - stats_began)))
                self.verify_with_monitor(verifier_id, arguments, directory / f'verification-{attempt:03d}.log',
                    timeout=remaining, check=verify_monitor)
                self.call(['docker', 'cp', verifier_id + ':' + internal + '/' + filename, str(directory / filename)])
                verification = json.loads((directory / filename).read_text())
                evidence_file = directory / f'database-evidence-{attempt:03d}.jsonl'
                self.call(['docker', 'cp', verifier_id + ':' + internal + '/' + evidence_name, str(evidence_file)])
                evidence_file.chmod(0o600)
                if verification.get('safety_stop') is not False:
                    raise previous.SafetyStop('independent_database_integrity_failure')
                if not statistics_retry_needed(verification):
                    break
                for _ in range(5):
                    inspected_at = self.check_monitor(observer, observation, guard, baseline, inspected_at)
                    time.sleep(1)
                attempt += 1
            browser_records = [record for record in records if record.get('account_id') in browser_ids]
            browser_ok = len(browser_records) == len(browser_ids) and all(record.get('status') == 'completed' for record in browser_records)
            generator_valid = driver_measurement_valid(done)
            verdict = previous.cell_verdict(summary, verification, monitor_ok=True)
            verdict['passed'] &= browser_ok and generator_valid
            diagnostics = collect_diagnostics(directory, self.diagnostic_manifest, started, time.time(), records=records)
            if not diagnostics['diagnostic_parse_complete']:
                verdict.update(passed=False, safety_stop=True)
            result = {**cell, **verdict, 'run_id': identity, 'browser_passed': browser_ok,
                'evidence_identity': self.evidence_identity(cell['candidate']),
                'measurement_version': contract.MEASUREMENT_VERSION, 'metric_schema_version': contract.METRIC_SCHEMA_VERSION,
                'generator_valid': generator_valid, 'driver_state': done.get('state'), 'driver_error': done.get('error'),
                'statistics_followup': 'not_retried_incomplete_workflow' if verification.get('workflow_complete') is False else 'normal',
                'counts': summary['counts'], 'diagnostics': diagnostics,
                'timings': {key: summary.get(key) for key in ('login', 'startup', 'save', 'save_scheduled', 'navigation',
                    'pointer_scheduled', 'pointer_http', 'submission', 'processing', 'completion')}, 'stats': verification.get('stats'),
                'evidence': str(directory.relative_to(self.root)), 'parameters': previous.parameters(config)}
            if not generator_valid:
                result['safety_stop'] = True
            shutil.copy2(evidence_file, directory / 'database-evidence.jsonl')
            self.add_observed_metrics(result, records, directory, verification)
        except BaseException as failure:
            error = failure
            self.publish_json(self.bridge / 'request.json', request | {'state': 'safety_stopped'})
            self.preserve_interrupted_cell(directory, cell, protocol, identity, failure)
            raise
        finally:
            stop_file.touch()
            try:
                observer.wait(timeout=35)
            except subprocess.TimeoutExpired:
                observer.terminate()
                observer.wait(timeout=5)
            observer_log.close()
            try:
                self.stop_collector()
                if error is None and (observer.returncode != 0 or not observation.exists()
                                      or json.loads(observation.read_text()).get('state') != 'completed'):
                    raise previous.SafetyStop('runtime_observation_incomplete')
            except BaseException as final_error:
                self.preserve_finalization_failure(directory, cell, identity, final_error, error)
                if error is not None:
                    raise error from final_error
                raise
        result['monitor_ok'] = True
        previous.write_once(directory / 'cell-result.json', result)
        return result

    def stage_v3(self, stage, names, modes=contract.MODES, *, counts=(50, 180), rounds=1, workload='paper-20q'):
        rows = []
        for cell in contract.retest_cells(stage, names, modes, counts=counts, rounds=rounds):
            reused = getattr(self, 'comparison_reuse', {}).get((cell['count'], cell['mode'])) if stage == 'comparison' else None
            if reused is not None:
                rows.append(reused)
                continue
            if stage in ('qualification', 'tuning'):
                identity = self.evidence_identity(cell['candidate'])
                cached = next((row for row in reversed(self.results)
                    if row.get('evidence_identity') == identity and row.get('count') == cell['count']
                    and row.get('mode') == cell['mode'] and row.get('workload') == workload
                    and row.get('browser_count', 0) == cell['browser_count'] and reusable_measurement(row)), None)
                if cached is not None:
                    rows.append(cached)
                    continue
            cell['workload'] = workload
            cell['execution_index'] = len(self.results) + 1
            self.report['active_cell'] = cell
            self.save_report()
            row = self.execute(cell)
            self.results.append(row)
            self.report['browser_operability_measured'] = any(item.get('browser_observations') for item in self.results)
            rows.append(row)
            self.report.update(active_cell=None, phase='cell_completed')
            self.save_report()
            print(json.dumps({key: row[key] for key in ('stage', 'candidate', 'count', 'mode', 'passed', 'counts')}), flush=True)
            if row['safety_stop']:
                raise previous.SafetyStop('cell_integrity_generator_or_monitor_failure')
            if stage == 'final' and not row['passed']:
                break
        return rows

    def run(self):
        try:
            config = json.loads((self.root / 'private/compose-R0.json').read_text())
            self.register('R0', config)
            resume_path = getattr(self, 'resume_comparisons', None)
            if resume_path:
                reused = load_comparison_resume(self.root, resume_path, self.fixture_bytes, config, self.manifest['project'])
                self.results.extend(reused)
                self.comparison_reuse = {(row['count'], row['mode']): row for row in reused}
                self.report['comparison_resume'] = {'report': str(Path(resume_path).resolve()),
                    'report_sha256': hashlib.sha256(Path(resume_path).read_bytes()).hexdigest(), 'reused_cells': len(reused)}
                self.save_report()
            result_path = getattr(self, 'resume_results', None)
            if result_path:
                reused = load_result_resume(self.root, result_path, self.fixture_bytes,
                    self.evidence_identity('R0'), self.manifest['project'])
                for row in reused:
                    if row.get('stage') == 'comparison':
                        key = row['count'], row['mode']
                        if key in self.comparison_reuse:
                            continue
                        self.comparison_reuse[key] = row
                    self.results.append(row)
                self.report['result_resume'] = {'report': str(Path(result_path).resolve()),
                    'report_sha256': hashlib.sha256(Path(result_path).read_bytes()).hexdigest(), 'reused_cells': len(reused)}
                self.save_report()
            self.register('C1', previous.modified(config, {'ASSESSMENT_REMAINING_DB_CONNECTIONS': '12'}))
            order = contract.MODES
            rows = self.stage_v3('qualification', ('C1',), order)
            reference = self.stage_v3('comparison', ('R0',), order)
            decision = contract.compare_candidate(reference, rows, 'assessment-pool')
            self.selections.append({'candidate': 'C1', 'parent_candidate': 'R0',
                'dimension': 'assessment-pool', 'status': 'accepted' if decision['improved'] else 'not_accepted',
                **decision})
            candidate = 'C1' if decision['improved'] else 'R0'
            if candidate == 'R0':
                rows = reference
            self.save_report()
            excluded = set()
            misses = {}
            number = 1
            axis_last = {}
            pending = None
            while True:
                failed = [row for row in rows if not row['passed']]
                chosen = next(((row, bottleneck_dimension(row, excluded)) for row in failed
                               if bottleneck_dimension(row, excluded)), None)
                if pending and (not failed or chosen is None or chosen[1] != pending['dimension']):
                    reverse = self.stage_v3('reverse-control', (candidate, pending['parent']), pending['modes'])
                    decision = contract.compare_candidate([row for row in reverse if row['candidate'] == pending['parent']],
                        [row for row in reverse if row['candidate'] == candidate], pending['dimension'])
                    self.selections.append({'candidate': candidate, 'parent_candidate': pending['parent'],
                        'dimension': pending['dimension'], 'stage': 'reverse-confirmation', **decision})
                    if not decision['improved']:
                        candidate = pending['parent']
                        excluded.add(pending['dimension'])
                    rows = self.stage_v3('qualification', (candidate,), tuple(dict.fromkeys(row['mode'] for row in rows)))
                    pending = None
                    self.save_report()
                    continue
                if failed:
                    if chosen is None:
                        self.report.update(state='completed_without_qualified_configuration',
                            failure_reason='No observed supported configuration step remains; preserve bottleneck evidence',
                            unresolved_cells=[{key: row.get(key) for key in ('count', 'mode', 'run_id', 'timings', 'diagnostics')} for row in failed])
                        return
                    row, dimension = chosen
                    old_config = self.configs[candidate][1]
                    parameters = {key: value for key, value in previous.parameters(old_config).items() if not key.startswith('resource.')}
                    changes = contract.grow_candidate(axis_last.get(dimension, parameters), dimension)
                    axis_last[dimension] = changes
                    try:
                        changes = resource_headroom(old_config, changes, row.get('diagnostics', {}))
                    except ValueError as resource_error:
                        excluded.add(dimension)
                        self.selections.append({'parent_candidate': candidate, 'dimension': dimension,
                            'improved': False, 'reason': str(resource_error), 'physical_resource_declined': True})
                        self.save_report()
                        continue
                    number += 1
                    name = 'C' + str(number)
                    self.register(name, previous.modified(old_config, changes))
                    affected = contract.affected_modes(dimension, {item['mode'] for item in rows})
                    reference = self.stage_v3('qualification', (candidate,), affected)
                    comparison = self.stage_v3('tuning', (name,), affected)
                    decision = contract.compare_candidate(reference, comparison, dimension)
                    self.selections.append({'candidate': name, 'parent_candidate': candidate, 'dimension': dimension,
                        'parameters': {key: value for key, value in changes.items() if value != parameters.get(key)},
                        'affected_modes': affected, 'status': 'tentative' if decision['improved'] else 'rejected', **decision})
                    if decision['improved']:
                        if pending is None:
                            pending = {'parent': candidate, 'dimension': dimension, 'modes': affected}
                        candidate, rows = name, comparison
                        misses[dimension] = 0
                        axis_last.clear()
                        excluded.clear()
                    else:
                        misses[dimension] = misses.get(dimension, 0) + 1
                        if misses[dimension] >= 2:
                            excluded.add(dimension)
                    self.save_report()
                    continue
                # Do not rerun already measured modes just to fill the next one.
                present = {(row['count'], row['mode']) for row in rows}
                missing = next((mode for mode in order if not {(50, mode), (180, mode)} <= present), None)
                if missing:
                    rows.extend(self.stage_v3('qualification', (candidate,), (missing,)))
                    continue
                selected = 'selected-' + candidate
                self.register(selected, self.configs[candidate][1])
                final = self.stage_v3('final', (selected,), rounds=2)
                if contract.formal_passed(final):
                    self.report.update(state='completed', formal_capacity_accepted=True, frozen_candidate=candidate,
                        frozen_config=str(self.configs[selected][0]))
                    return
                failed_final = [row for row in final if not row['passed']]
                if not failed_final:
                    self.report.update(state='completed_with_incomplete_final', formal_capacity_accepted=False)
                    return
                failed_keys = {(row['count'], row['mode']) for row in failed_final}
                rows = [row for row in rows if (row['count'], row['mode']) not in failed_keys] + failed_final
                self.report.update(phase='formal_failure_returned_to_tuning', formal_capacity_accepted=False)
                self.save_report()
        except BaseException as error:
            self.report.update(state='safety_stopped', error_type=type(error).__name__,
                failure_reason=str(error) if isinstance(error, previous.SafetyStop) else 'See private controller exception')
            if hasattr(self, 'diagnostic_check_failure'):
                self.report['diagnostic_check_failure'] = self.diagnostic_check_failure
            raise
        finally:
            self.save_report()
            self.publish_controller(self.report['state'])
            self.stop_collector()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-manifest', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--resume-comparisons', help='Preserved owned report; reuse only fully verified same-source R0 comparisons')
    parser.add_argument('--resume-results', help='Preserved finalized same-identity cells; does not reuse formal acceptance rounds')
    args = parser.parse_args()
    manifest_path = Path(args.run_manifest).resolve()
    os.environ[contract.MANIFEST_ENV] = str(manifest_path)
    manifest = contract.validate_manifest(json.loads(manifest_path.read_text()))
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise SystemExit('Authorized isolated DUT operator required')
    if not args.run_id.isalnum() or len(args.run_id) > 24:
        raise SystemExit('Unique alphanumeric run-id of at most24 characters required')
    RetestMatrix(args.run_id, manifest, args.resume_comparisons, args.resume_results).run()


if __name__ == '__main__':
    main()
