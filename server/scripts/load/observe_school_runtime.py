#!/usr/bin/env python3
"""Read-only Docker OOM events and bounded memory samples for one Compose project."""
import argparse
from collections import Counter
import json
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from school_resource_budget import budget


_sample_deadline = None
_sample_retry_used = False
_sample_command_failures = []


def begin_sample_budget():
    global _sample_deadline, _sample_retry_used, _sample_command_failures
    _sample_deadline = time.monotonic() + 25
    _sample_retry_used = False
    _sample_command_failures = []


def command_timeout(requested=5):
    remaining = 5 if _sample_deadline is None else _sample_deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Runtime sample CLI budget exhausted')
    return min(requested, remaining)


def docker_output(command, **kwargs):
    return docker_call(subprocess.check_output, command, **kwargs)


def docker_run(command, **kwargs):
    return docker_call(subprocess.run, command, **kwargs)


def docker_call(execute, command, **kwargs):
    global _sample_retry_used
    requested = min(5, kwargs.get('timeout', 5))
    while True:
        kwargs['timeout'] = command_timeout(requested)
        started = time.monotonic()
        try:
            return execute(command, **kwargs)
        except subprocess.TimeoutExpired:
            _sample_command_failures.append({'command': command[:2], 'at': time.time(),
                'duration_seconds': round(time.monotonic() - started, 6),
                'error_type': 'TimeoutExpired'})
            if _sample_deadline is None or _sample_retry_used:
                raise
            _sample_retry_used = True


def discover_runtime(cache, *, project, extra_ids=()):
    identities = docker_output(['docker', 'ps', '-q', '--no-trunc', '--filter',
        'label=com.docker.compose.project=' + project], text=True).split()
    identities = list(dict.fromkeys([*identities, *extra_ids]))
    items, lifecycle = inspect_containers(identities)
    now = time.monotonic()
    cache.update(identities=set(identities), items=items, inspected_at=now, confirmed_at=time.time())
    return items, lifecycle, 0


def sample_delay(until, now, interval=2, *, sampled_at=None):
    remaining = interval if sampled_at is None else interval - (now - sampled_at)
    return max(0, min(remaining, until - now))


def observer_http_roles(configuration):
    if configuration.get('pool_profile') in ('shared-entry-v1', 'shared-entry-v2', 'shared-entry-v3'):
        return {'entry', 'assessment', 'capture', 'web', 'teacher-read'}
    roles = {'web', 'student-read', 'teacher-read'}
    if configuration.get('pool_profile') == 'six-pools-180-v1':
        roles.update(('auth', 'assessment', 'capture'))
    if configuration.get('roles', {}).get('startup', {}).get('actual_running') is True:
        roles.add('startup')
    return roles


def observer_required_roles(configuration):
    roles = observer_http_roles(configuration) | {'scheduler', 'nginx', 'pgbouncer'}
    if configuration.get('pool_profile') in ('shared-entry-v2', 'shared-entry-v3'):
        roles.update(('session-submission-worker', 'analytics-worker'))
    roles.update(role for role in ('analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker', 'session-submission-worker')
                 if configuration.get('roles', {}).get(role, {}).get('actual_running') is True)
    if configuration.get('postgres_profile') == 'isolated-pg17-v1':
        roles.add('db')
    if configuration.get('pool_profile') in ('six-pools-180-v1', 'shared-entry-v1', 'shared-entry-v2', 'shared-entry-v3'):
        roles.update(role for role in ('redis-session', 'redis-admission') if role in configuration.get('roles', {}))
    return roles


def capacity_runtime_budget(items, configuration, project):
    """Check observed replica counts and budgets from actual backend environments."""
    if configuration.get('pool_profile') not in ('shared-entry-v2', 'shared-entry-v3'):
        return None
    backend_roles = observer_http_roles(configuration) | {'scheduler', 'session-submission-worker', 'analytics-worker'}
    rows = [item for item in items if required_service(item, project, backend_roles)
            and item['State']['Running'] and item['State']['Pid'] > 0]
    counts = Counter(item['Config']['Labels']['com.docker.compose.service'] for item in rows)
    if counts['entry'] != 1:
        raise ValueError('Capacity runtime entry replica count changed')
    environment = [dict(value.split('=', 1) for value in item['Config'].get('Env', []) if '=' in value) for item in rows]
    keys = ('ASSESSMENT_ENTRY_WORKERS', 'ASSESSMENT_ENTRY_DB_CONNECTIONS',
        'ASSESSMENT_REMAINING_WORKERS', 'ASSESSMENT_REMAINING_DB_CONNECTIONS',
        'ASSESSMENT_CAPTURE_WORKERS', 'ASSESSMENT_CAPTURE_DB_CONNECTIONS',
        'ASSESSMENT_SUBMISSION_WORKERS', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS',
        'ASSESSMENT_ANALYTICS_WORKERS', 'ASSESSMENT_BACKGROUND_DB_CONNECTIONS',
        'SCHOOL_DB_MAX_CONNECTIONS', 'SCHOOL_DB_RESERVED_CONNECTIONS',
        'SCHOOL_DB_DIRECT_CONNECTIONS', 'SCHOOL_DB_MONITOR_CONNECTIONS',
        'SCHOOL_DB_MAINTENANCE_CONNECTIONS', 'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS')
    v3 = configuration.get('pool_profile') == 'shared-entry-v3'
    if v3:
        keys += ('ASSESSMENT_CORE_WORKERS', 'ASSESSMENT_CORE_DB_CONNECTIONS',
            'ASSESSMENT_TEACHER_WORKERS', 'ASSESSMENT_TEACHER_DB_CONNECTIONS',
            'ASSESSMENT_ENTRY_LOGIN_LIMIT', 'ASSESSMENT_ENTRY_STARTUP_LIMIT',
            'ASSESSMENT_ENTRY_HEAVY_LIMIT', 'ASSESSMENT_ADMISSION_CAPACITY',
            'ASSESSMENT_ENTRY_STARTUP_RATE', 'ASSESSMENT_ENTRY_STARTUP_BURST', 'PGBOUNCER_MAX_CLIENT_CONN')
        keys += tuple(f'ASSESSMENT_{role}_DB_RESERVE_CONNECTIONS' for role in
            ('ENTRY', 'REMAINING', 'CAPTURE', 'CORE', 'TEACHER', 'BACKGROUND', 'SUBMISSION'))
    try:
        values = [{key: int(env[key]) for key in keys} for env in environment]
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError('Missing or invalid capacity runtime budget') from error
    if any(value != values[0] for value in values) or any(env.get('SCHOOL_POOL_PROFILE') != configuration.get('pool_profile') for env in environment):
        raise ValueError('Capacity runtime environments are inconsistent')
    value = values[0]
    submission = value['ASSESSMENT_SUBMISSION_WORKERS']
    analytics = value['ASSESSMENT_ANALYTICS_WORKERS']
    expected_counts = {role: 1 for role in backend_roles}
    expected_counts.update({'session-submission-worker': submission, 'analytics-worker': analytics})
    if dict(counts) != expected_counts:
        raise ValueError('Capacity runtime replica count changed')
    expected_workers = {'entry': value['ASSESSMENT_ENTRY_WORKERS'],
        'assessment': value['ASSESSMENT_REMAINING_WORKERS'], 'capture': value['ASSESSMENT_CAPTURE_WORKERS'],
        'web': value['ASSESSMENT_CORE_WORKERS'] if v3 else 1,
        'teacher-read': value['ASSESSMENT_TEACHER_WORKERS'] if v3 else 1}
    for item, env in zip(rows, environment):
        role = item['Config']['Labels']['com.docker.compose.service']
        if role in expected_workers and env.get('GUNICORN_WORKERS') != str(expected_workers[role]):
            raise ValueError('Capacity runtime HTTP worker count changed')
    pools = {alias: {'pool_size': size, 'reserve_pool_size': reserve} for alias, size, reserve in (
        ('kt_entry', value['ASSESSMENT_ENTRY_DB_CONNECTIONS'], 0),
        ('kt_assessment', value['ASSESSMENT_REMAINING_DB_CONNECTIONS'], 2),
        ('kt_capture', value['ASSESSMENT_CAPTURE_DB_CONNECTIONS'], 1), ('kt_core', 1, 0), ('kt_teacher', 1, 0),
        ('kt_background', value['ASSESSMENT_BACKGROUND_DB_CONNECTIONS'], 0),
        ('kt_submission', value['ASSESSMENT_SUBMISSION_DB_CONNECTIONS'], 0))}
    if v3:
        for alias, role in (('kt_core', 'CORE'), ('kt_teacher', 'TEACHER')):
            pools[alias]['pool_size'] = value[f'ASSESSMENT_{role}_DB_CONNECTIONS']
        for alias, role in (('kt_entry', 'ENTRY'), ('kt_assessment', 'REMAINING'), ('kt_capture', 'CAPTURE'),
                ('kt_core', 'CORE'), ('kt_teacher', 'TEACHER'), ('kt_background', 'BACKGROUND'),
                ('kt_submission', 'SUBMISSION')):
            pools[alias]['reserve_pool_size'] = value[f'ASSESSMENT_{role}_DB_RESERVE_CONNECTIONS']
    reserves = sum(pool['reserve_pool_size'] for pool in pools.values())
    ordinary = sum(pool['pool_size'] for pool in pools.values())
    planned_total = ordinary + reserves + sum(value[key] for key in ('SCHOOL_DB_DIRECT_CONNECTIONS',
        'SCHOOL_DB_MONITOR_CONNECTIONS', 'SCHOOL_DB_MAINTENANCE_CONNECTIONS', 'SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS'))
    available = value['SCHOOL_DB_MAX_CONNECTIONS'] - value['SCHOOL_DB_RESERVED_CONNECTIONS']
    if (min(value.values()) < 0 or any(pool['pool_size'] < 1 for pool in pools.values())
            or planned_total > available or min(expected_workers.values()) < 1 or min(submission, analytics) < 1
            or value['ASSESSMENT_SUBMISSION_DB_CONNECTIONS'] < submission
            or value['ASSESSMENT_BACKGROUND_DB_CONNECTIONS'] < 2 * analytics
            or (not v3 and (value['SCHOOL_DB_MAX_CONNECTIONS'] != 64 or value['SCHOOL_DB_RESERVED_CONNECTIONS'] != 3
                or value['SCHOOL_DB_DIRECT_CONNECTIONS'] < 1 or value['SCHOOL_DB_MONITOR_CONNECTIONS'] < 2
                or value['SCHOOL_DB_MAINTENANCE_CONNECTIONS'] < 2 or submission not in (1, 2, 4)
                or value['ASSESSMENT_SUBMISSION_DB_CONNECTIONS'] != submission or analytics not in (1, 2)))
            or (v3 and (min(value['ASSESSMENT_ENTRY_STARTUP_RATE'], value['ASSESSMENT_ENTRY_STARTUP_BURST'],
                value['PGBOUNCER_MAX_CLIENT_CONN']) < 1
                or not 1 <= value['ASSESSMENT_ENTRY_LOGIN_LIMIT'] <= value['ASSESSMENT_ENTRY_HEAVY_LIMIT'] < value['ASSESSMENT_ENTRY_WORKERS']
                or not 1 <= value['ASSESSMENT_ENTRY_STARTUP_LIMIT'] <= value['ASSESSMENT_ENTRY_HEAVY_LIMIT']
                or not 1 <= value['ASSESSMENT_ADMISSION_CAPACITY'] <= min(value['ASSESSMENT_ENTRY_STARTUP_LIMIT'],
                    value['ASSESSMENT_ENTRY_WORKERS'], value['ASSESSMENT_ENTRY_DB_CONNECTIONS'])))):
        raise ValueError('Capacity runtime exceeds its connection budget')
    return {'http_workers': sum(expected_workers.values()), 'ordinary_connections': ordinary,
        'pool_reserve_connections': reserves, 'planned_total_connections': planned_total,
        'available_nonreserved_connections': available, 'submission_replicas': submission,
        'analytics_replicas': analytics, 'pools': pools,
        **({'max_client_connections': value['PGBOUNCER_MAX_CLIENT_CONN']} if v3 else {})}


def observer_resource_scope(item, configuration, project, database_ids):
    labels = item['Config'].get('Labels', {})
    if item['Id'] in database_ids:
        if (configuration.get('postgres_profile') == 'isolated-pg17-v1'
                and labels.get('com.docker.compose.project') == project
                and labels.get('com.docker.compose.service') == 'db'):
            return 'isolated_test'
        return 'shared_postgres'
    return 'isolated_test' if labels.get('com.docker.compose.project') == project else 'production_or_other'


def probe_progress(path, now):
    if path is None:
        return {'state': 'not_configured'}
    try:
        sample = json.loads(Path(path).read_text(encoding='utf-8'))
        age = now - sample['at']
        return {'state': 'sampled' if 0 <= age <= 10 else 'stale', 'age_seconds': age,
                **{key: sample.get(key) for key in
                   ('postgres_guard', 'postgres_available', 'postgres_probe', 'native_postgres_probe')}}
    except FileNotFoundError:
        return {'state': 'missing'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'state': 'invalid'}


def publish_progress(output, started, sample):
    journal = Path(output).with_suffix('.samples.jsonl')
    with journal.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(sample) + '\n')
        stream.flush()
    journal.chmod(0o644)
    path = Path(output).with_suffix('.progress.json')
    previous = json.loads(path.read_text()) if path.exists() else {}
    complete = sample if sample.get('state') == 'sampled' else previous.get('latest_sample')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'state':'observing', 'started_at':started, 'heartbeat_at':time.time(),
        'freshness_clock':'monotonic', 'heartbeat_monotonic':time.monotonic(),
        'formal_capacity_accepted':False, 'latest_sample': complete, 'current_sample': sample,
        'last_complete_sample_at': complete.get('at') if complete else None}, indent=2))
    temporary.chmod(0o644)
    os.replace(temporary, path)


BACKLOG_SQL = """SELECT json_build_object(
 'research', (SELECT count(*) FROM analytics_researchsessionmaterialization WHERE status IN ('queued','running')),
 'research_failed', (SELECT count(*) FROM analytics_researchsessionmaterialization WHERE status='failed'),
 'comparison', (SELECT count(*) FROM experiments_classexperimenttask t LEFT JOIN analytics_studentclasstaskcomparison c ON c.class_task_id=t.id WHERE t.result_revision>0 AND (c.id IS NULL OR c.status<>'ready' OR c.source_revision<>t.result_revision)),
 'export', (SELECT count(*) FROM analytics_exportjob WHERE status IN ('queued','running')),
 'export_failed', (SELECT count(*) FROM analytics_exportjob WHERE status='failed'))"""

DATABASE_WAIT_SQL = """SELECT coalesce(json_agg(row_to_json(w)), '[]'::json) FROM (
 SELECT 'isolated_test' AS scope, a.pid, a.application_name,
        coalesce(a.wait_event_type,'not_waiting') AS wait_event_type, a.wait_event, a.state, 1 AS connections,
        pg_blocking_pids(a.pid) AS blocking_pids,
        extract(epoch FROM clock_timestamp()-a.xact_start) AS transaction_age_seconds,
        extract(epoch FROM clock_timestamp()-a.query_start) AS query_age_seconds,
        md5(a.query) AS query_fingerprint,
        ARRAY(SELECT DISTINCT c.relname FROM pg_locks l JOIN pg_class c ON c.oid=l.relation
              WHERE l.pid=a.pid AND NOT l.granted) AS waiting_relations
 FROM pg_stat_activity a WHERE a.datname='kt_e2e' AND a.pid<>pg_backend_pid()
 UNION ALL
 SELECT 'other_database', NULL, NULL, coalesce(wait_event_type,'not_waiting'), NULL, state,
        count(*)::integer, NULL, NULL, NULL, NULL, NULL
 FROM pg_stat_activity WHERE datname IS DISTINCT FROM 'kt_e2e' AND pid<>pg_backend_pid()
 GROUP BY wait_event_type,state) w"""

DATABASE_WAL_SQL = """SELECT json_build_object(
 'scope','shared_postgres_instance', 'wal_records',wal_records,
 'wal_bytes',wal_bytes, 'wal_buffers_full',wal_buffers_full,
 'wal_write',wal_write, 'wal_sync',wal_sync,
 'wal_write_time_ms',wal_write_time, 'wal_sync_time_ms',wal_sync_time,
 'io_timing_enabled',current_setting('track_wal_io_timing'), 'stats_reset',stats_reset)
 FROM pg_stat_wal"""


def host_cpu_counters(proc=Path('/proc')):
    lines = (proc / 'stat').read_text().splitlines()
    values = [int(value) for value in lines[0].split()[1:]]
    names = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal', 'guest', 'guest_nice')
    result = {name + '_ticks': value for name, value in zip(names, values)}
    result.update({key: int(value) for key, value in (line.split() for line in lines
                   if line.startswith(('procs_running ', 'procs_blocked ')))})
    result['run_queue'] = int((proc / 'loadavg').read_text().split()[3].split('/')[0])
    return result


def host_io_counters(proc=Path('/proc')):
    result = {}
    for line in (proc / 'diskstats').read_text().splitlines():
        fields = line.split()
        values = [int(value) for value in fields[3:]]
        if len(values) >= 11:
            result[fields[2]] = {'read_bytes': values[2] * 512, 'write_bytes': values[6] * 512,
                'io_in_progress': values[8], 'io_milliseconds': values[9],
                'weighted_io_milliseconds': values[10]}
    return result


def measure_metric(name, collect, status):
    started_at, started = time.time(), time.monotonic()
    metadata = {'started_at': started_at}
    try:
        value = collect()
        metadata['state'] = 'measured'
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        value = None
        metadata.update(state='missing', error_type=type(error).__name__)
    metadata.update(ended_at=time.time(), duration_seconds=round(time.monotonic() - started, 6))
    status[name] = metadata
    return value


def http_listen_queues(pid, proc=Path('/proc')):
    rows = []
    for protocol in ('tcp', 'tcp6'):
        table = proc / str(pid) / 'net' / protocol
        if not table.exists():
            continue
        for line in table.read_text().splitlines()[1:]:
            values = line.split()
            if values[3] == '0A' and int(values[1].split(':')[1], 16) == 8000:
                tx, rx = (int(value, 16) for value in values[4].split(':'))
                rows.append({'port': 8000, 'tx_queue': tx, 'rx_queue': rx})
    return rows


def kernel_summary(container, kernel, at, previous):
    prior = previous.get(container)
    cpu = None
    if prior and at > prior[0]:
        cpu = f"{max(0, kernel['cpu']['usage_usec'] - prior[1]) / ((at - prior[0]) * 10000):.3f}%"
    return {'at': at, 'container': container,
            'memory': f"{kernel['memory_current_bytes']} / {kernel['memory_max']}", 'cpu': cpu,
            'measurement': 'cgroupv2_delta'}


def finish_observation(output, result, error):
    missing = {}
    samples = result.get('host_database_samples', [])
    for sample in samples:
        for name, measurement in sample.get('measurement_status', {}).items():
            if measurement.get('state') == 'missing':
                missing[name] = missing.get(name, 0) + 1
    failed_samples = sum(sample.get('state') == 'failed' for sample in samples)
    result.update(state='failed' if error is not None or missing or failed_samples else 'completed', ended_at=time.time(),
                  missing_measurements=missing, missing_measurement_count=sum(missing.values()),
                  failed_sample_count=failed_samples,
                  formal_capacity_accepted=False)
    if error is not None:
        result['error_type'] = type(error).__name__
    with Path(output).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    Path(output).chmod(0o644)


def cgroup_metrics(group, *, root=Path('/sys/fs/cgroup')):
    group = Path(group)
    def pairs(name):
        return {key: int(value) for key, value in (line.split() for line in (group / name).read_text().splitlines())}
    io_rows = []
    for line in (group / 'io.stat').read_text().splitlines():
        device, *values = line.split()
        io_rows.append({'device': device, **{key: int(value) for key, value in (word.split('=') for word in values)}})
    current = int((group / 'memory.current').read_text())
    values = pairs('memory.stat') if (group / 'memory.stat').exists() else {}
    clean = max(0, min(values.get('inactive_file', 0), values.get('file', 0))
                - values.get('file_dirty', 0) - values.get('file_writeback', 0))
    result = {'cpu': pairs('cpu.stat'), 'memory_events': pairs('memory.events'),
            'memory_current_bytes': current, 'working_set_bytes': max(0, current-clean), 'io': io_rows}
    swap = group / 'memory.swap.current'
    result['memory_swap_current_bytes'] = int(swap.read_text()) if swap.exists() else None
    result.update({name.replace('.', '_'): (group/name).read_text().strip() for name in
                   ('cpu.max','cpu.weight','memory.max','memory.swap.max') if (group/name).exists()})
    # A container may be unthrottled locally while its slice or another parent is capped.
    group, root = group.resolve(), Path(root).resolve()
    ancestors = [group]
    if group.is_relative_to(root):
        current_group = group
        while current_group != root:
            current_group = current_group.parent
            ancestors.append(current_group)
    constraints = []
    cpu_quotas, memory_limits = [], []
    for ancestor in ancestors:
        row = {'path': str(ancestor)}
        for name in ('cpu.max', 'cpu.weight', 'cpuset.cpus.effective', 'cpuset.mems.effective',
                'memory.max', 'memory.high', 'memory.swap.max'):
            path = ancestor / name
            if path.exists():
                row[name.replace('.', '_')] = path.read_text().strip()
        quota = row.get('cpu_max', '').split()
        if len(quota) == 2 and quota[0] != 'max':
            cpu_quotas.append(int(quota[0]) / int(quota[1]))
        memory = row.get('memory_max')
        if memory is not None and memory != 'max':
            memory_limits.append(int(memory))
        constraints.append(row)
    result['cpuset_cpus_effective'] = constraints[0].get('cpuset_cpus_effective')
    result['ancestry_constraints'] = constraints
    result['effective_cpu_quota_cores'] = min(cpu_quotas) if cpu_quotas else None
    result['effective_memory_max_bytes'] = min(memory_limits) if memory_limits else None
    return result


def cgroup_path(item):
    pid = item['State']['Pid']
    unified = [line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::')]
    if len(unified) != 1:
        raise ValueError('Observer requires cgroupv2')
    group = (Path('/sys/fs/cgroup') / unified[0].lstrip('/')).resolve()
    if not group.is_relative_to(Path('/sys/fs/cgroup')) or item['Id'] not in str(group):
        raise ValueError('Observer container cgroup identity mismatch')
    return group


def is_oneoff(item):
    return item['Config'].get('Labels', {}).get('com.docker.compose.oneoff', '').lower() == 'true'


def required_service(item, project, roles):
    labels = item['Config'].get('Labels', {})
    return (labels.get('com.docker.compose.project') == project
            and labels.get('com.docker.compose.service') in roles and not is_oneoff(item))


def sampled_service_roles(items, project):
    return {item['Config']['Labels']['com.docker.compose.service'] for item in items
            if item['Config'].get('Labels', {}).get('com.docker.compose.project') == project and not is_oneoff(item)}


def reinspect_container(container_id):
    result = docker_run(['docker', 'inspect', container_id], text=True, capture_output=True, timeout=30)
    if result.returncode:
        remaining = docker_output(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + container_id],
                                            text=True, stderr=subprocess.PIPE, timeout=30).split()
        if container_id not in remaining:
            return None
        raise ValueError('Runtime container reinspection failed')
    items = json.loads(result.stdout)
    if len(items) != 1 or items[0]['Id'] != container_id:
        raise ValueError('Runtime reinspection container identity mismatch')
    return items[0]


def inspect_containers(identities):
    if not identities:
        return [], []
    result = docker_run(['docker', 'inspect', *identities], text=True, capture_output=True, timeout=30)
    items = json.loads(result.stdout)
    found = {item['Id']: item for item in items}
    if len(found) != len(items) or not found.keys() <= set(identities):
        raise ValueError('Runtime batch inspection container identity mismatch')
    missing = [identity for identity in identities if identity not in found]
    if result.returncode and not missing:
        raise ValueError('Runtime batch container inspection failed')
    lifecycle = []
    for identity in missing:
        item = reinspect_container(identity)
        if item is None:
            lifecycle.append({'status': 'skipped_removed_during_discovery', 'container_id': identity})
        else:
            found[identity] = item
    return [found[identity] for identity in identities if identity in found], lifecycle


def sample_container(item, *, required):
    """Return actual metrics or an evidenced optional stop; retry changed PID once."""
    original_pid = item['State']['Pid']
    try:
        if not item['State']['Running'] or original_pid <= 0:
            raise FileNotFoundError()
        metrics = cgroup_metrics(cgroup_path(item))
        return {'container': item, 'metrics': metrics, 'lifecycle': {'status': 'stable', 'container_id': item['Id']}}
    except FileNotFoundError:
        fresh = reinspect_container(item['Id'])
        if fresh is None or not fresh['State']['Running'] or fresh['State']['Pid'] <= 0:
            if required:
                raise ValueError('Required runtime container stopped during sampling') from None
            return {'container': fresh or item, 'metrics': None, 'lifecycle': {
                'status': 'skipped_removed_optional' if fresh is None else 'skipped_stopped_optional',
                'container_id': item['Id'], 'discovered_pid': original_pid}}
        if fresh['State']['Pid'] == original_pid:
            raise ValueError('Active runtime process missing with unchanged PID') from None
        try:
            metrics = cgroup_metrics(cgroup_path(fresh))
        except FileNotFoundError:
            raise ValueError('Runtime process disappeared after one restart retry') from None
        return {'container': fresh, 'metrics': metrics, 'lifecycle': {'status': 'restarted_retried',
            'container_id': item['Id'], 'discovered_pid': original_pid, 'sampled_pid': fresh['State']['Pid']}}


def pool_metrics(project, configuration=None, *, capacity=None):
    import csv
    configuration = configuration or {}
    profile = configuration.get('pool_profile')
    aliases = {'kt_core', 'kt_auth', 'kt_assessment', 'kt_capture', 'kt_student',
               'kt_teacher', 'kt_background', 'kt_startup', 'kt_submission'}
    expected = set()
    if profile in ('shared-entry-v1', 'shared-entry-v2', 'shared-entry-v3'):
        expected = aliases = {'kt_entry', 'kt_core', 'kt_assessment', 'kt_capture',
                              'kt_teacher', 'kt_background', 'kt_submission'}
    elif profile == 'six-pools-180-v1':
        expected = aliases - {'kt_startup', 'kt_submission'}
        for role, alias in (('startup', 'kt_startup'), ('session-submission-worker', 'kt_submission')):
            if configuration.get('roles', {}).get(role, {}).get('actual_running') is True:
                expected.add(alias)
    ids = docker_output(['docker','ps','-q','--filter',f'label=com.docker.compose.project={project}',
        '--filter','label=com.docker.compose.service=pgbouncer'],text=True, timeout=5).split()
    if len(ids) != 1:
        raise ValueError('Isolated PgBouncer unavailable for measurement')
    result = {}
    for command in ('SHOW POOLS','SHOW STATS'):
        observed = set()
        raw = docker_output(['docker','exec',ids[0],'sh','-c',
            'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -A -F "|" -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -c "$1"',
            'school-pool-observer',command],text=True, timeout=5)
        for row in csv.DictReader(raw.splitlines(), delimiter='|'):
            if row.get('database') not in aliases:
                continue
            alias = row['database']
            observed.add(alias)
            result.setdefault(alias,{})
            for key in ('cl_active','cl_waiting','sv_active','sv_idle','sv_used','sv_tested','sv_login',
                        'maxwait','total_xact_count','total_xact_time','total_query_count',
                        'total_query_time','total_wait_time','avg_query_time','avg_wait_time'):
                if key in row:
                    result[alias][key] = float(row[key])
        if expected - observed:
            raise ValueError(f'{command} missing required pools: {", ".join(sorted(expected - observed))}')
    if profile in ('shared-entry-v2', 'shared-entry-v3'):
        if capacity is None:
            raise ValueError('Capacity pool measurement requires actual runtime budgets')
        raw = docker_output(['docker', 'exec', ids[0], 'sh', '-c',
            'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -A -F "|" -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -c "SHOW DATABASES"'],
            text=True, timeout=5)
        observed = {}
        for row in csv.DictReader(raw.splitlines(), delimiter='|'):
            alias = row.get('name')
            if alias in aliases:
                observed[alias] = {key: int(row[key]) for key in ('pool_size', 'reserve_pool_size')}
        for alias, expected_pool in capacity['pools'].items():
            if observed.get(alias) != expected_pool:
                raise ValueError(f'PgBouncer configured allocation changed: {alias}')
            result[alias].update({'configured_' + key: value for key, value in expected_pool.items()})
    if profile == 'shared-entry-v3':
        raw = docker_output(['docker', 'exec', ids[0], 'sh', '-c',
            'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -A -F "|" -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -c "SHOW CONFIG"'],
            text=True, timeout=5)
        observed_config = {row['key']: row['value'] for row in csv.DictReader(raw.splitlines(), delimiter='|')}
        if int(observed_config.get('max_client_conn', '0')) != capacity['max_client_connections']:
            raise ValueError('PgBouncer configured client connection limit changed')
        for alias in capacity['pools']:
            result[alias]['configured_max_client_connections'] = capacity['max_client_connections']
    return result


def swap_used_bytes():
    try:
        fields = {line.split(':')[0]: int(line.split()[1]) * 1024
                  for line in Path('/proc/meminfo').read_text().splitlines()
                  if line.startswith(('SwapTotal:', 'SwapFree:'))}
        return fields['SwapTotal'] - fields['SwapFree']
    except (OSError, KeyError):
        return None


def memory_available_bytes():
    try:
        return next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    except (OSError, StopIteration):
        return None


def capacity_memory_stop(window, now, available, total):
    if window.get('last_at') is None or now - window['last_at'] >= 15:
        window['last_at'] = now
        window['threshold_bytes'] = max(2 * 1024**3, total * 0.15)
        window['low_samples'] = window.get('low_samples', 0) + 1 if available < window['threshold_bytes'] else 0
    return window.get('low_samples', 0) >= 3


def swap_cached_bytes():
    try:
        return next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.startswith('SwapCached:'))
    except (OSError, StopIteration):
        return None


def stop_test_project(project, database_ids):
    identities = docker_output(['docker', 'ps', '-q', '--no-trunc', '--filter',
        'label=com.docker.compose.project=' + project], text=True).split()
    stopped = []
    for identity in identities:
        item = reinspect_container(identity)
        if item is None or item['Id'] in database_ids:
            continue
        labels = item['Config'].get('Labels', {})
        if labels.get('com.docker.compose.project') == project and labels.get('com.docker.compose.service') != 'db':
            stopped.append(item['Id'])
    if stopped:
        docker_run(['docker', 'stop', '--time', '3', *stopped], capture_output=True, check=True)


def new_oom(item, kernel, configuration, project, database_ids):
    labels = item['Config'].get('Labels', {})
    if item['Id'] in database_ids and configuration.get('postgres_profile') != 'isolated-pg17-v1':
        baseline = configuration.get('shared_postgres_cgroup', {})
    elif labels.get('com.docker.compose.project') == project:
        baseline = configuration.get('roles', {}).get(labels.get('com.docker.compose.service'), {}).get('cgroup', {})
    else:
        return False
    return kernel.get('memory_events', {}).get('oom_kill', 0) > baseline.get('memory_events', {}).get('oom_kill', 0)


def recent_oom_events(project, database_ids, since, until):
    output = docker_output(['docker', 'events', '--since', str(int(since)),
        '--until', str(int(until)), '--filter', 'type=container', '--filter', 'event=oom',
        '--format', '{{json .}}'], text=True)
    rows = []
    for line in output.splitlines():
        event = json.loads(line)
        actor = event.get('Actor', {})
        if actor.get('ID') in database_ids or actor.get('Attributes', {}).get('com.docker.compose.project') == project:
            rows.append(event)
    return rows


def partial_sample(cache, started_at):
    status = {}
    rows = []
    for item in cache.get('items', []):
        labels = item.get('Config', {}).get('Labels', {})
        row = {'container_id': item['Id'], 'role': labels.get('com.docker.compose.service'),
            'identity_confirmed_at': cache.get('confirmed_at'), 'sampled_at': time.time()}
        try:
            row.update(state='sampled', cgroup=cgroup_metrics(cgroup_path(item)))
        except (OSError, ValueError, KeyError) as error:
            row.update(state='missing', error_type=type(error).__name__)
        rows.append(row)
    return {'state': 'partial', 'at': time.time(), 'sample_started_at': started_at,
        'phase': 'host_and_known_cgroups', 'resource_rows': rows,
        'memory_available_bytes': memory_available_bytes(),
        'host_cpu_counters': measure_metric('host_cpu_counters', host_cpu_counters, status),
        'host_io_counters': measure_metric('host_io_counters', host_io_counters, status),
        'host_swap_used_bytes': swap_used_bytes(), 'host_swap_cached_bytes': swap_cached_bytes(),
        'measurement_status': status}


def observe(args, result):
    global _sample_deadline
    _sample_deadline = None
    started = time.time()
    until = started + args.duration
    ids = docker_output(["docker", "ps", "-q", "--filter",
                                  f"label=com.docker.compose.project={args.project}"], text=True).split()
    if not ids:
        raise SystemExit("No running containers in this Compose project")
    db_ids = [args.database_container] if args.database_container else docker_output(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={args.project}", "--filter", "label=com.docker.compose.service=db"], text=True).split()
    if len(db_ids) != 1:
        raise SystemExit("Requires one isolated test database service")
    db_ids = [docker_output(["docker", "inspect", "--format", "{{.Id}}", item], text=True).strip() for item in db_ids]
    swap_start = swap_used_bytes()
    events_command = ["docker", "events", "--since", str(int(started)), "--until", str(int(until) + 1),
                      "--filter", "type=container", "--filter", "event=oom"]
    events_command.extend(["--format", "{{json .}}"])
    samples = []
    backlog = []
    system_samples = []
    peak_rows = {}
    previous_cpu = {}
    discovery_cache = {}
    memory_window = {}
    result.update(started_at=started, memory_cpu_samples=samples, backlog_samples=backlog,
                  host_database_samples=system_samples, swap_start_bytes=swap_start,
                  swap_cached_start_bytes=swap_cached_bytes())
    configuration = json.loads((Path(args.output).parent / 'source-config.json').read_text())
    required_roles = observer_required_roles(configuration)
    http_roles = observer_http_roles(configuration)
    isolated_postgres = configuration.get('postgres_profile') == 'isolated-pg17-v1'
    stop_file = Path(args.stop_file) if args.stop_file else None
    events = subprocess.Popen(events_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    oom_checked_until = started
    sample_pending = False
    sample_phase = 'initialization'
    try:
        while time.time() < until and not (stop_file and stop_file.exists()):
            now = time.time()
            sample_started = time.monotonic()
            sample_pending = True
            sample_phase = 'oom_events'
            begin_sample_budget()
            partial = partial_sample(discovery_cache, now)
            publish_progress(args.output, started, partial)
            recent_oom = recent_oom_events(args.project, db_ids, oom_checked_until, now)
            oom_checked_until = now
            if recent_oom:
                result.update(emergency_stop_reason='docker_oom_event', oom_events=recent_oom,
                              oom_kills=len(recent_oom))
                stop_test_project(args.project, db_ids)
                raise SystemExit('Test containers stopped: OOM occurred; acceptance failed')
            available = partial['memory_available_bytes']
            capacity_retest = args.project == 'kt-school-e2e-capacity-retest-20261016'
            total = (next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                if line.startswith('MemTotal:')) if capacity_retest else None)
            low_memory = (capacity_memory_stop(memory_window, sample_started, available, total)
                if capacity_retest and available is not None else
                available is None or available < args.abort_memory_below_mb * 1024**2)
            if low_memory:
                # Stop only the explicitly named isolated test project, preserving production.
                stop_test_project(args.project, db_ids)
                raise SystemExit('Test containers stopped: insufficient host memory headroom; acceptance failed')
            # Extra resources must be explicitly identified in the source record.
            discovery_started = time.monotonic()
            sample_phase = 'container_discovery'
            inspected, lifecycle, inspection_age = discover_runtime(discovery_cache,
                project=args.project, extra_ids=[*db_ids, *configuration.get('observed_container_ids', [])])
            discovery_duration = round(time.monotonic() - discovery_started, 6)
            resource_rows = []; sampled_roles = set(); postgres_kernel = None; capacity_items = []
            sample_phase = 'container_resources'
            for item in inspected:
                project = item['Config'].get('Labels', {}).get('com.docker.compose.project')
                role = item['Config'].get('Labels', {}).get('com.docker.compose.service')
                sampled = sample_container(item, required=item['Id'] in db_ids or required_service(item, args.project, required_roles))
                lifecycle.append(sampled['lifecycle'])
                if sampled['metrics'] is None:
                    continue
                item, kernel = sampled['container'], sampled['metrics']
                capacity_items.append(item)
                if new_oom(item, kernel, configuration, args.project, db_ids):
                    result['emergency_stop_reason'] = 'new_oom_kill'
                    stop_test_project(args.project, db_ids)
                    raise SystemExit('Test containers stopped: OOM occurred; acceptance failed')
                if item['Id'] in db_ids:
                    postgres_kernel = kernel
                sampled_roles.update(sampled_service_roles([item], args.project))
                row = {'container_id': item['Id'], 'scope': observer_resource_scope(item, configuration, args.project, db_ids),
                    'role': role, 'working_set_bytes': kernel['working_set_bytes'], 'memory_max': kernel['memory_max'], 'cgroup': kernel}
                if project == args.project and role in http_roles:
                    row['http_listen_queues'] = http_listen_queues(item['State']['Pid'])
                resource_rows.append(row)
                at = time.time()
                summary = kernel_summary(item['Name'].lstrip('/'), kernel, at, previous_cpu)
                previous_cpu[item['Name'].lstrip('/')] = (at, kernel['cpu']['usage_usec'])
                samples.append({**summary, 'scope': row['scope']})
                prior = peak_rows.get(item['Id'])
                if not prior or prior['working_set_bytes'] < row['working_set_bytes']:
                    peak_rows[item['Id']] = row
            if postgres_kernel is None or not required_roles <= sampled_roles:
                raise ValueError('Required database or runtime role missing from observation')
            capacity = capacity_runtime_budget(capacity_items, configuration, args.project)
            if total is None:
                total = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))
            measurement_status = dict(partial['measurement_status'])
            sample_phase = 'database_and_pool_metrics'
            waits = measure_metric('database_waits', lambda: json.loads(docker_output(['docker', 'exec', db_ids[0], 'sh', '-c',
                'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"',
                'school-wait-observer', DATABASE_WAIT_SQL], text=True, timeout=5)), measurement_status)
            wal = measure_metric('database_wal', lambda: json.loads(docker_output(['docker', 'exec', db_ids[0], 'sh', '-c',
                'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"',
                'school-wal-observer', DATABASE_WAL_SQL], text=True, timeout=5)), measurement_status)
            if wal is not None:
                wal['scope'] = 'isolated_postgres_instance' if isolated_postgres else 'shared_postgres_instance'
            counters = partial['host_cpu_counters']
            pools = measure_metric('pgbouncer', lambda: pool_metrics(args.project, configuration, capacity=capacity)
                if capacity is not None else pool_metrics(args.project, configuration), measurement_status)
            counts = measure_metric('backlog', lambda: json.loads(docker_output([
                'docker', 'exec', db_ids[0], 'sh', '-c',
                'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"', 'school-observer', BACKLOG_SQL,
            ], text=True, timeout=5)), measurement_status)
            backlog.append({'at': measurement_status['backlog']['ended_at'], 'counts': counts,
                            'measurement': measurement_status['backlog']})
            failed = any(row['state'] == 'missing' for row in measurement_status.values())
            duration = round(time.monotonic() - sample_started, 6)
            interval = getattr(args, 'interval', 2)
            system_samples.append({'at': time.time(), 'sample_started_at': now,
                'sample_started_monotonic': sample_started,
                'sample_duration_seconds': duration, 'requested_interval_seconds': interval,
                'container_discovery_duration_seconds': discovery_duration,
                'container_inspection_age_seconds': inspection_age,
                'sampling_overrun_seconds': round(max(0, duration - interval), 6),
                'command_timeouts': list(_sample_command_failures),
                'state': 'failed' if failed or duration > 30 else 'sampled', 'measurement_status': measurement_status,
                'memory_available_bytes': available, 'host_cpu_counters': counters,
                'memory_total_bytes': total, 'memory_window': dict(memory_window),
                'host_io_counters': partial['host_io_counters'],
                'database_wal': wal,
                **({'isolated_wal': wal} if isolated_postgres else {'shared_wal': wal}),
                'postgres_profile': configuration.get('postgres_profile'),
                'host_swap_used_bytes': swap_used_bytes(), 'host_swap_cached_bytes': swap_cached_bytes(),
                'postgres_cgroup': postgres_kernel, 'database_waits': waits, 'sampling_lifecycle': lifecycle,
                'database_wait_measurement': 'instantaneous_blocking_sample_not_exact_lock_duration',
                'database_probe': probe_progress(getattr(args, 'database_probe_progress', None), time.time()),
                'pgbouncer': pools,
                'resource_rows': resource_rows, 'memory_budget': budget(resource_rows, total, available)})
            if capacity is not None:
                system_samples[-1]['capacity_budget'] = capacity
            publish_progress(args.output, started, system_samples[-1])
            sample_pending = False
            time.sleep(sample_delay(until, time.time(), interval, sampled_at=now))
        early_stop = bool(stop_file and stop_file.exists())
        terminated_by_coordinator = early_stop and events.poll() is None
        if terminated_by_coordinator:
            events.terminate()
        output, _ = events.communicate(timeout=15)
        result.update(oom_observer_exit_code=events.returncode,
                      oom_observer_stopped_by_coordinator=terminated_by_coordinator)
        if events.returncode != 0 and not (terminated_by_coordinator and events.returncode in (-15, 143)):
            raise SystemExit("Docker OOM observation failed")
        # The load client container starts after observation. Filter event labels
        # here so its OOM cannot disappear from a fixed initial container list.
        event_rows = []
        for line in output.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            actor = event.get("Actor", {})
            if actor.get("ID") in db_ids or actor.get("Attributes", {}).get("com.docker.compose.project") == args.project:
                event_rows.append(event)
    except BaseException as error:
        if sample_pending:
            interrupted = {'state': 'failed', 'at': time.time(), 'sample_started_at': now,
                'sample_duration_seconds': round(time.monotonic() - sample_started, 6),
                'phase': sample_phase, 'measurement_status': {sample_phase: {
                    'state': 'missing', 'error_type': type(error).__name__}},
                'command_timeouts': list(_sample_command_failures)}
            result['interrupted_sample'] = interrupted
            publish_progress(args.output, started, interrupted)
        raise
    finally:
        _sample_deadline = None
        if events.poll() is None:
            events.terminate()
            events.wait(timeout=10)
    # The coordinator signals after drain; the last periodic sample may have
    # preceded that drain by several seconds. Read its actual final state.
    final_backlog = docker_output([
        'docker', 'exec', db_ids[0], 'sh', '-c',
        'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"', 'school-observer', BACKLOG_SQL,
    ], text=True)
    backlog.append({'at': time.time(), 'counts': json.loads(final_backlog)})
    freshness_sql = f"""SELECT json_build_object(
      'terminal_count', count(*),
      'initial_count', count(first_ready.activated_at),
      'initial_p95_seconds', percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM (first_ready.activated_at-s.terminal_at))),
      'current_count', count(m.materialization_id),
      'latest_count', count(s.research_source_changed_at) FILTER (WHERE m.materialization_id IS NOT NULL),
      'latest_p95_seconds', percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM (m.activated_at-s.research_source_changed_at))))
      FROM assessment_experimentsession s LEFT JOIN analytics_researchsessionmaterialization m
      ON m.session_id=s.id AND m.is_current AND m.status='succeeded' AND m.source_revision=s.research_source_revision
      LEFT JOIN LATERAL (SELECT min(activated_at) AS activated_at FROM analytics_researchsessionmaterialization first_m
        WHERE first_m.session_id=s.id AND first_m.status='succeeded' AND first_m.activated_at>=s.terminal_at) first_ready ON true
      WHERE s.terminal_at>=to_timestamp({started})"""
    freshness = {'measured': False, 'reason': 'short_capture_background_workers_disabled'} if args.scope == 'short' else json.loads(docker_output([
        'docker', 'exec', db_ids[0], 'sh', '-c',
        'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"', 'school-observer', freshness_sql,
    ], text=True))
    peak_budget = budget(list(peak_rows.values()), total, min(row['memory_available_bytes'] for row in system_samples))
    peak_budget['combined_working_set_bytes'] = max(row['memory_budget']['combined_working_set_bytes'] for row in system_samples)
    peak_budget['checks']['combined_working_set_plus_margin'] = all(row['memory_budget']['checks']['combined_working_set_plus_margin'] for row in system_samples)
    result.update({"source": "docker_events", "project": args.project,
              "started_at": started, "ended_at": time.time(),
              "observed_duration_seconds": round(time.time() - started, 3),
              "oom_kills": len(event_rows), "memory_cpu_samples": samples,
              'host_database_samples': system_samples, 'stopped_by_coordinator': early_stop,
              'peak_memory_budget': peak_budget,
              "teacher_freshness": freshness,
              "swap_start_bytes": swap_start, "swap_end_bytes": swap_used_bytes(),
              'swap_cached_end_bytes': swap_cached_bytes(), "backlog_samples": backlog})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--duration', type=int, default=2100)
    parser.add_argument('--interval', type=int, choices=(1, 2, 5, 10), default=2)
    parser.add_argument('--output', required=True)
    parser.add_argument('--database-container')
    parser.add_argument('--database-probe-progress')
    parser.add_argument('--abort-memory-below-mb', type=int, default=2048)
    parser.add_argument('--stop-file')
    parser.add_argument('--scope', choices=('short', 'formal'), default='formal')
    args = parser.parse_args()
    if args.duration <= 0 or not args.project.startswith('kt-school-e2e'):
        raise SystemExit('Positive duration and isolated kt-school-e2e project required')
    if any(Path(args.output).with_suffix(suffix).exists() for suffix in ('.json', '.samples.jsonl', '.progress.json')):
        raise SystemExit('Observation evidence already exists; refusing replacement')
    result = {'state': 'observing', 'project': args.project, 'scope': args.scope}
    try:
        observe(args, result)
    except BaseException as error:
        finish_observation(args.output, result, error)
        raise
    finish_observation(args.output, result, None)
    print(json.dumps({key: value for key, value in result.items() if key not in ("memory_cpu_samples", "backlog_samples", 'host_database_samples')}))


if __name__ == "__main__":
    main()
