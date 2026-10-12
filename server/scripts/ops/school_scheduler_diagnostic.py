#!/usr/bin/env python3
"""Fixed read-only scheduler exit/lock diagnostic before school resource binding."""
import json
import os
import re
import subprocess
from pathlib import Path

RUN = '20261010T115729Z-358195'
PROJECT = 'kt-school-e2e'
PRIVATE = Path('/home/ktdata/school-cache-private') / RUN
PUBLIC = Path('/home/ktdata/school-cache-reports') / RUN
LOCK_ID = 4_819_520_817
LOCK_CLASSID, LOCK_OBJID = LOCK_ID >> 32, LOCK_ID & 0xffffffff
LOCK_SQL = """SELECT l.pid,
 CASE WHEN a.application_name='kt-school-scheduler-direct' THEN 'candidate_scheduler'
      WHEN a.application_name='kt-scheduler' THEN 'production_scheduler'
      WHEN a.application_name='kt-school-core' THEN 'candidate_core'
      ELSE 'other' END AS application_class,
 count(*) AS holder_count, bool_or(l.granted) AS granted
 FROM pg_locks l JOIN pg_database d ON d.oid=l.database
 LEFT JOIN pg_stat_activity a ON a.pid=l.pid
 WHERE l.locktype='advisory' AND d.datname=current_database()
 AND l.classid=%s AND l.objid=%s AND l.objsubid=1
 GROUP BY l.pid, application_class ORDER BY l.pid"""
DB_CODE = """import os,json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
from django.db import connection,transaction
with transaction.atomic(), connection.cursor() as cursor:
 cursor.execute('SET TRANSACTION READ ONLY')
 cursor.execute("SET LOCAL statement_timeout='10s'")
 cursor.execute("SET LOCAL lock_timeout='2s'")
 cursor.execute('SELECT current_database(),pg_backend_pid()')
 database,backend=cursor.fetchone()
 holders=[]
 if database=='kt_e2e':
  cursor.execute(SQL,PARAMS)
  holders=[dict(zip(('backend_pid','application_class','holder_count','granted'),row)) for row in cursor.fetchall()]
print(json.dumps({'current_database_is_kt_e2e':database=='kt_e2e','observer_backend_pid':backend,'lock_holders':holders}))
""".replace('SQL,PARAMS', repr(LOCK_SQL) + ',' + repr((LOCK_CLASSID, LOCK_OBJID)))


def log_metadata(text):
    return {'known_lock_held_message': any(line.strip() == 'scheduler lock is held by another process'
                                         for line in text.splitlines())}


def scheduler_metadata(item):
    labels = item['Config'].get('Labels', {})
    if labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.service') != 'scheduler':
        raise ValueError('Exact isolated scheduler diagnostic identity required')
    cmd = item['Config'].get('Cmd') or []
    values = dict(value.split('=', 1) for value in item['Config'].get('Env', []) if '=' in value)
    return {'runs_scheduler': 'run_scheduler' in cmd, 'has_once': '--once' in cmd,
        'database': 'kt_e2e' if values.get('POSTGRES_DB') == 'kt_e2e' else 'unexpected_database',
        'host': values.get('POSTGRES_HOST') if values.get('POSTGRES_HOST') in {'school-test-postgres', 'db', 'pgbouncer'} else 'unexpected_host',
        'pool_alias': values.get('POSTGRES_POOL_ALIAS') if values.get('POSTGRES_POOL_ALIAS') in {'', 'kt_core', 'kt_student', 'kt_teacher', 'kt_background'} else 'unexpected_pool_alias',
        'application_class': 'candidate_scheduler' if values.get('DATABASE_APPLICATION_NAME') == 'kt-school-scheduler-direct' else 'other',
        'RestartCount': item.get('RestartCount'),
        **{key: item['State'].get(key) for key in ('Pid', 'Running', 'Restarting', 'ExitCode', 'StartedAt', 'FinishedAt', 'OOMKilled')}}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE, timeout=30).strip()


def one_service(role, *, running):
    ids = docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT,
                 '--filter', 'label=com.docker.compose.service=' + role).split()
    items = json.loads(docker('inspect', *ids)) if ids else []
    items = [item for item in items if item['Config'].get('Labels', {}).get('com.docker.compose.oneoff', '').lower() != 'true']
    if len(items) != 1 or not re.fullmatch('[a-f0-9]{64}', items[0]['Id']) or (running and not items[0]['State']['Running']):
        raise ValueError('Single main isolated diagnostic service required')
    return items[0]


def main():
    os.umask(0o077)
    binding_path = PRIVATE / 'cached-source-binding.json'
    if os.geteuid() != 0 or binding_path.is_symlink() or binding_path.stat().st_uid != 0 or binding_path.stat().st_mode & 0o077:
        raise ValueError('Root private scheduler diagnostic binding required')
    binding = json.loads(binding_path.read_text())
    if binding.get('run_id') != RUN or not re.fullmatch('[a-f0-9]{64}', binding.get('candidate_archive_sha256', '')):
        raise ValueError('Exact school scheduler diagnostic run required')
    from school_post_formal_verification import write_json
    scheduler = one_service('scheduler', running=False)
    logs = subprocess.run(['docker', 'logs', '--tail', '150', scheduler['Id']], text=True, capture_output=True, timeout=30)
    if logs.returncode: raise ValueError('Isolated scheduler diagnostic logs unavailable')
    log = logs.stdout + '\n' + logs.stderr
    private_log = PRIVATE / ('scheduler-diagnostic-' + binding['candidate_archive_sha256'][:16] + '.log')
    with private_log.open('x') as output: output.write(log)
    private_log.chmod(0o600)
    value = {'run_id': RUN, 'source_sha256': binding['candidate_archive_sha256'],
        'frontend_sha256': binding['frontend_archive_sha256'], 'read_only': True,
        'scheduler': scheduler_metadata(scheduler), **log_metadata(log), 'database': None,
        'raw_logs_private': True, 'advisory_lock_acquired': False, 'processes_killed': False}
    write_json(PUBLIC / 'scheduler-config-diagnostic.json', value, 0o644)
    web = one_service('web', running=True)
    result = subprocess.run(['docker', 'exec', web['Id'], 'python', '-c', DB_CODE],
                            text=True, capture_output=True, timeout=30)
    if result.returncode:
        private_error = PRIVATE / ('scheduler-database-diagnostic-' + binding['candidate_archive_sha256'][:16] + '.log')
        with private_error.open('x') as output: output.write(result.stderr)
        private_error.chmod(0o600)
        raise ValueError('Read-only isolated scheduler lock lookup failed')
    database = json.loads(result.stdout)
    if set(database) != {'current_database_is_kt_e2e', 'observer_backend_pid', 'lock_holders'}:
        raise ValueError('Unexpected scheduler database diagnostic shape')
    for row in database['lock_holders']:
        if (set(row) != {'backend_pid', 'application_class', 'holder_count', 'granted'}
                or row['application_class'] not in {'candidate_scheduler', 'production_scheduler', 'candidate_core', 'other'}
                or type(row['backend_pid']) is not int or type(row['holder_count']) is not int or type(row['granted']) is not bool):
            raise ValueError('Unexpected scheduler lock holder diagnostic shape')
    write_json(PUBLIC / 'scheduler-config-diagnostic.json', {**value, 'database': database}, 0o644)
    if database['current_database_is_kt_e2e'] is not True:
        raise ValueError('Scheduler diagnostic connection is not isolated test database')
    print('Read-only isolated scheduler diagnostic published', flush=True)


if __name__ == '__main__': main()
