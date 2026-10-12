#!/usr/bin/env python3
"""Read-only Docker OOM events and bounded memory samples for one Compose project."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from school_resource_budget import budget


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
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'state':'observing', 'started_at':started,
        'formal_capacity_accepted':False, 'latest_sample':sample}, indent=2))
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
    values = [int(value) for value in (proc / 'stat').read_text().splitlines()[0].split()[1:]]
    names = ('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal', 'guest', 'guest_nice')
    return {name + '_ticks': value for name, value in zip(names, values)}


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
    result.update(state='failed' if error is not None else 'completed', ended_at=time.time(),
                  formal_capacity_accepted=False)
    if error is not None:
        result['error_type'] = type(error).__name__
    with Path(output).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    Path(output).chmod(0o644)


def cgroup_metrics(group):
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
    result = subprocess.run(['docker', 'inspect', container_id], text=True, capture_output=True, timeout=30)
    if result.returncode:
        remaining = subprocess.check_output(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'id=' + container_id],
                                            text=True, stderr=subprocess.PIPE, timeout=30).split()
        if container_id not in remaining:
            return None
        raise ValueError('Runtime container reinspection failed')
    items = json.loads(result.stdout)
    if len(items) != 1 or items[0]['Id'] != container_id:
        raise ValueError('Runtime reinspection container identity mismatch')
    return items[0]


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


def pool_metrics(project):
    import csv
    ids = subprocess.check_output(['docker','ps','-q','--filter',f'label=com.docker.compose.project={project}',
        '--filter','label=com.docker.compose.service=pgbouncer'],text=True).split()
    if len(ids) != 1:
        raise ValueError('Isolated PgBouncer unavailable for measurement')
    result = {}
    for command in ('SHOW POOLS','SHOW STATS'):
        raw = subprocess.check_output(['docker','exec',ids[0],'sh','-c',
            'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -A -F "|" -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -c "$1"',
            'school-pool-observer',command],text=True)
        for row in csv.DictReader(raw.splitlines(), delimiter='|'):
            if row.get('database') not in ('kt_core','kt_student','kt_teacher','kt_background'):
                continue
            alias = row['database']
            result.setdefault(alias,{})
            for key in ('cl_active','cl_waiting','sv_active','sv_idle','maxwait','total_query_count',
                        'total_query_time','total_wait_time','avg_query_time','avg_wait_time'):
                if key in row:
                    result[alias][key] = float(row[key])
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


def swap_cached_bytes():
    try:
        return next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.startswith('SwapCached:'))
    except (OSError, StopIteration):
        return None


def stop_test_project(project, database_ids):
    identities = subprocess.check_output(['docker', 'ps', '-q', '--no-trunc', '--filter',
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
        subprocess.run(['docker', 'stop', *stopped], capture_output=True, check=True)


def new_oom(item, kernel, configuration, project, database_ids):
    labels = item['Config'].get('Labels', {})
    if item['Id'] in database_ids:
        baseline = configuration.get('shared_postgres_cgroup', {})
    elif labels.get('com.docker.compose.project') == project:
        baseline = configuration.get('roles', {}).get(labels.get('com.docker.compose.service'), {}).get('cgroup', {})
    else:
        return False
    return kernel.get('memory_events', {}).get('oom_kill', 0) > baseline.get('memory_events', {}).get('oom_kill', 0)


def recent_oom_events(project, database_ids, since, until):
    output = subprocess.check_output(['docker', 'events', '--since', str(int(since)),
        '--until', str(int(until)), '--filter', 'type=container', '--filter', 'event=oom',
        '--format', '{{json .}}'], text=True)
    rows = []
    for line in output.splitlines():
        event = json.loads(line)
        actor = event.get('Actor', {})
        if actor.get('ID') in database_ids or actor.get('Attributes', {}).get('com.docker.compose.project') == project:
            rows.append(event)
    return rows


def observe(args, result):
    started = time.time()
    until = started + args.duration
    ids = subprocess.check_output(["docker", "ps", "-q", "--filter",
                                  f"label=com.docker.compose.project={args.project}"], text=True).split()
    if not ids:
        raise SystemExit("No running containers in this Compose project")
    db_ids = [args.database_container] if args.database_container else subprocess.check_output(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={args.project}", "--filter", "label=com.docker.compose.service=db"], text=True).split()
    if len(db_ids) != 1:
        raise SystemExit("Requires one isolated test database service")
    db_ids = [subprocess.check_output(["docker", "inspect", "--format", "{{.Id}}", item], text=True).strip() for item in db_ids]
    swap_start = swap_used_bytes()
    events_command = ["docker", "events", "--since", str(int(started)), "--until", str(int(until) + 1),
                      "--filter", "type=container", "--filter", "event=oom"]
    events_command.extend(["--format", "{{json .}}"])
    samples = []
    backlog = []
    system_samples = []
    peak_rows = {}
    previous_cpu = {}
    result.update(started_at=started, memory_cpu_samples=samples, backlog_samples=backlog,
                  host_database_samples=system_samples, swap_start_bytes=swap_start,
                  swap_cached_start_bytes=swap_cached_bytes())
    configuration = json.loads((Path(args.output).parent / 'source-config.json').read_text())
    required_roles = {'web', 'student-read', 'teacher-read', 'scheduler', 'nginx', 'pgbouncer'}
    required_roles.update(role for role in ('analytics-worker', 'student-comparison-worker', 'publication-worker', 'export-worker')
                          if configuration.get('roles', {}).get(role, {}).get('actual_running') is True)
    stop_file = Path(args.stop_file) if args.stop_file else None
    events = subprocess.Popen(events_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    oom_checked_until = started
    try:
        while time.time() < until and not (stop_file and stop_file.exists()):
            now = time.time()
            recent_oom = recent_oom_events(args.project, db_ids, oom_checked_until, now)
            oom_checked_until = now
            if recent_oom:
                result.update(emergency_stop_reason='docker_oom_event', oom_events=recent_oom,
                              oom_kills=len(recent_oom))
                stop_test_project(args.project, db_ids)
                raise SystemExit('Test containers stopped: OOM occurred; acceptance failed')
            available = memory_available_bytes()
            if available is None or available < args.abort_memory_below_mb * 1024**2:
                # Stop only the explicitly named isolated test project, preserving production.
                stop_test_project(args.project, db_ids)
                raise SystemExit('Test containers stopped: insufficient host memory headroom; acceptance failed')
            # Sample the current load generator and production siblings too;
            # the shared PostgreSQL container appears exactly once.
            live = subprocess.check_output(['docker', 'ps', '-q', '--no-trunc'], text=True).split()
            inspected = [item for identity in live if (item := reinspect_container(identity)) is not None]
            resource_rows = []; lifecycle = []; sampled_roles = set(); postgres_kernel = None
            for item in inspected:
                project = item['Config'].get('Labels', {}).get('com.docker.compose.project')
                role = item['Config'].get('Labels', {}).get('com.docker.compose.service')
                sampled = sample_container(item, required=item['Id'] in db_ids or required_service(item, args.project, required_roles))
                lifecycle.append(sampled['lifecycle'])
                if sampled['metrics'] is None:
                    continue
                item, kernel = sampled['container'], sampled['metrics']
                if new_oom(item, kernel, configuration, args.project, db_ids):
                    result['emergency_stop_reason'] = 'new_oom_kill'
                    stop_test_project(args.project, db_ids)
                    raise SystemExit('Test containers stopped: OOM occurred; acceptance failed')
                if item['Id'] in db_ids:
                    postgres_kernel = kernel
                sampled_roles.update(sampled_service_roles([item], args.project))
                row = {'container_id': item['Id'], 'scope': 'shared_postgres' if item['Id'] in db_ids
                    else 'isolated_test' if project == args.project else 'production_or_other',
                    'role': role, 'working_set_bytes': kernel['working_set_bytes'], 'memory_max': kernel['memory_max'], 'cgroup': kernel}
                if project == args.project and role in ('web', 'student-read', 'teacher-read'):
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
            total = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))
            waits = json.loads(subprocess.check_output(['docker', 'exec', db_ids[0], 'sh', '-c',
                'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"',
                'school-wait-observer', DATABASE_WAIT_SQL], text=True))
            system_samples.append({'at': time.time(), 'memory_available_bytes': available,
                'host_cpu_counters': host_cpu_counters(),
                'shared_wal': json.loads(subprocess.check_output(['docker', 'exec', db_ids[0], 'sh', '-c',
                    'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"',
                    'school-wal-observer', DATABASE_WAL_SQL], text=True)),
                'host_swap_used_bytes': swap_used_bytes(), 'host_swap_cached_bytes': swap_cached_bytes(),
                'postgres_cgroup': postgres_kernel, 'database_waits': waits, 'sampling_lifecycle': lifecycle,
                'database_wait_measurement': 'instantaneous_blocking_sample_not_exact_lock_duration',
                'database_probe': probe_progress(getattr(args, 'database_probe_progress', None), time.time()),
                'pgbouncer': pool_metrics(args.project),
                'resource_rows': resource_rows, 'memory_budget': budget(resource_rows, total, available)})
            publish_progress(args.output, started, system_samples[-1])
            raw_backlog = subprocess.check_output([
                "docker", "exec", db_ids[0], "sh", "-c",
                'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e -c "$1"', "school-observer", BACKLOG_SQL,
            ], text=True)
            backlog.append({"at": time.time(), "counts": json.loads(raw_backlog)})
            time.sleep(min(10, max(0, until - time.time())))
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
    finally:
        if events.poll() is None:
            events.terminate()
            events.wait(timeout=10)
    # The coordinator signals after drain; the last periodic sample may have
    # preceded that drain by several seconds. Read its actual final state.
    final_backlog = subprocess.check_output([
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
    freshness = {'measured': False, 'reason': 'short_capture_background_workers_disabled'} if args.scope == 'short' else json.loads(subprocess.check_output([
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
