"""Create only the explicitly owned isolated school capacity project (root on DUT)."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import shutil
from capacity_retest import runtime_identity, validate_manifest, r0_parameters

ROOT, PROJECT, PORT = runtime_identity()


def call(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def private(path, value):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
        stream.write(value)


def rebind_retest_compose(config, manifest, backend_image, frontend_image):
    manifest = validate_manifest(manifest)
    result = copy.deepcopy(config)
    root = Path(manifest['root']).resolve()
    result['name'] = manifest['project']
    for kind in ('volumes', 'networks'):
        for name, value in result.get(kind, {}).items():
            if value.get('external'):
                raise ValueError('External resources cannot be cloned into the retest')
            value['name'] = manifest['project'] + '_' + name
    for role, service in result['services'].items():
        service.pop('build', None)
        service.pop('container_name', None)
        if role == 'nginx':
            service['image'] = frontend_image
            service['ports'] = [{'host_ip': '127.0.0.1', 'published': str(manifest['port']), 'target': 80, 'protocol': 'tcp'}]
        elif role not in ('db', 'pgbouncer', 'redis-admission', 'redis-session'):
            service['image'] = backend_image
            service.setdefault('environment', {}).update(SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED='1',
                SCHOOL_CAPTURE_DIAGNOSTICS_MODE='light', ASSESSMENT_REQUEST_TIMING_ENABLED='1')
            service.setdefault('environment', {})['DJANGO_CSRF_TRUSTED_ORIGINS'] = (
                f'http://127.0.0.1:{manifest["port"]},http://localhost:{manifest["port"]}')
        for mount in service.get('volumes', []):
            if mount['type'] == 'bind' and not Path(mount['source']).resolve().is_relative_to(root / 'source'):
                raise ValueError('Retest bind mount points outside its frozen source')
            if mount['type'] == 'volume' and mount['source'] not in result.get('volumes', {}):
                raise ValueError('Retest references an undeclared volume')
    return result


def cold_source_volumes(items):
    source_project = 'kt-school-e2e-capacity-20261016'
    if not items or any(item['Config'].get('Labels', {}).get('com.docker.compose.project') != source_project
                        or item['State']['Running'] for item in items):
        raise ValueError('Cold source copy requires every original project container to remain stopped')
    database = [item for item in items if item['Config']['Labels'].get('com.docker.compose.service') == 'db']
    if len(database) != 1:
        raise ValueError('Exactly one original database required')
    expected = {'postgres_data': source_project + '_postgres_data', 'media_data': source_project + '_media_data'}
    names = {mount.get('Name') for item in items for mount in item.get('Mounts', []) if mount['Type'] == 'volume'}
    db_names = {mount.get('Name') for mount in database[0]['Mounts']
                if mount['Destination'] == '/var/lib/postgresql/data' and mount['Type'] == 'volume'}
    if not set(expected.values()) <= names or db_names != {expected['postgres_data']}:
        raise ValueError('Source PostgreSQL/media volume identity differs')
    return expected


def clone_stopped_source(manifest):
    manifest = validate_manifest(manifest)
    root = Path(manifest['root'])
    root.mkdir(mode=0o700, exist_ok=True)
    for name in ('private', 'evidence', 'ops', 'source'):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    report_path = root / 'evidence/source-cold-copy.json'
    if report_path.exists():
        raise ValueError('Cold source copy already exists; refusing to replace it')
    def inspect_source():
        ids = call(['docker', 'ps', '-aq', '--filter',
                    'label=com.docker.compose.project=kt-school-e2e-capacity-20261016']).split()
        return json.loads(call(['docker', 'inspect', *ids])) if ids else []
    volumes = cold_source_volumes(inspect_source())
    copied = {}
    for kind, name in volumes.items():
        original = json.loads(call(['docker', 'volume', 'inspect', name]))[0]
        if original['Name'] != name or not Path(original['Mountpoint']).resolve().is_dir():
            raise ValueError('Source volume mount is absent or changed')
        source = Path(original['Mountpoint']).resolve()
        if kind == 'postgres_data' and (source / 'PG_VERSION').read_text().strip() != '17':
            raise ValueError('Original PostgreSQL version differs')
        backup = (root / 'private' / ('source-' + kind)).resolve()
        if not backup.is_relative_to((root / 'private').resolve()) or backup.exists():
            raise ValueError('Cold backup target must be a fresh owned path')
        shutil.copytree(source, backup, symlinks=True, copy_function=shutil.copy2)
        target_name = manifest['project'] + '_' + kind
        call(['docker', 'volume', 'create', '--label', 'com.docker.compose.project=' + manifest['project'],
              '--label', 'com.docker.compose.volume=' + kind, target_name])
        target_volume = json.loads(call(['docker', 'volume', 'inspect', target_name]))[0]
        destination = Path(target_volume['Mountpoint']).resolve()
        if (target_volume['Name'] != target_name or target_volume.get('Labels', {}).get('com.docker.compose.project') != manifest['project']
                or any(destination.iterdir())):
            raise ValueError('Cold restore target is not an empty new owned volume')
        shutil.copytree(backup, destination, symlinks=True, dirs_exist_ok=True, copy_function=shutil.copy2)
        copied[kind] = {'source_volume': name, 'target_volume': target_name, 'backup': str(backup)}
    cold_source_volumes(inspect_source())
    report = {'state': 'copied', 'project': manifest['project'], 'source_consistency': 'all_source_containers_stopped',
              'source_modified': False, 'copies': copied}
    private(report_path, json.dumps(report, indent=2))
    return report


def prepare_retest(archive_path, archive_sha256, manifest_path):
    """Render the new project; no old containers, volumes or configuration are changed."""
    manifest = validate_manifest(json.loads(Path(manifest_path).read_text(encoding='utf-8')))
    root = Path(manifest['root'])
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise ValueError('Retest preparation requires the authorized DUT operator')
    archive = Path(archive_path).resolve()
    if hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() != archive_sha256:
        raise ValueError('Source archive digest differs')
    if (root / 'evidence/compose-preflight.json').exists():
        raise ValueError('Retest preparation has already completed')
    if call(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + manifest['project']]).strip():
        raise ValueError('New retest project already has containers')
    root.mkdir(mode=0o700, exist_ok=True)
    for name in ('source', 'private', 'evidence', 'ops'):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    source = root / 'source'
    with tarfile.open(archive) as stream:
        if any(Path(row.name).is_absolute() or '..' in Path(row.name).parts or row.issym() or row.islnk()
               for row in stream.getmembers()):
            raise ValueError('Source archive has unsafe members')
        stream.extractall(source, filter='data')
    old_path = Path('/home/ktdata/student-capacity-tests/20261016/private/compose-W12D8.json')
    old = json.loads(old_path.read_text())
    if old.get('name') != 'kt-school-e2e-capacity-20261016':
        raise ValueError('Source configuration ownership differs')
    database = old['services']['db']['environment']
    environment = copy.deepcopy(old['services']['web']['environment'])
    origin = f'http://127.0.0.1:{manifest["port"]}'
    environment.update(POSTGRES_DB='kt_e2e', POSTGRES_USER=database['POSTGRES_USER'],
        POSTGRES_PASSWORD=database['POSTGRES_PASSWORD'], POSTGRES_HOST='pgbouncer', POSTGRES_PORT='5432',
        SCHOOL_RUNTIME_ISOLATION_ENABLED='1', SCHOOL_REDIS_SESSION_ENABLED='0',
        DJANGO_CSRF_TRUSTED_ORIGINS=f'{origin},http://localhost:{manifest["port"]}',
        DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1', CANONICAL_PUBLIC_ORIGIN='',
        DJANGO_SECURE_SSL_REDIRECT='0', DJANGO_SESSION_COOKIE_SECURE='0', DJANGO_CSRF_COOKIE_SECURE='0',
        ASSESSMENT_PREPARED_ENABLED='1', ASSESSMENT_TRANSITION_ENABLED='1', ASSESSMENT_TRANSITION_COURSE_IDS='*',
        ASSESSMENT_ADMISSION_ENABLED='1', ASSESSMENT_ADMISSION_GLOBAL_ENABLED='1',
        ASSESSMENT_PREPARED_ADMISSION_MODE='postgres_transaction', CLOSE_BUNDLE_ENABLED='1',
        SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED='1', SCHOOL_CAPTURE_DIAGNOSTICS_MODE='light',
        ASSESSMENT_REQUEST_TIMING_ENABLED='1', RUN_SCHEDULER_EXPORT_JOBS='0')
    parameters = r0_parameters() | manifest.get('r0', {})
    environment.update(parameters)
    text = ''.join(key + '=' + json.dumps(str(value)) + '\n' for key, value in environment.items())
    private(source / 'server/.env', text)
    private(source / 'deploy/school-test.env', text)
    chosen = os.environ | environment | {'SCHOOL_TEST_POSTGRES_USER': database['POSTGRES_USER'],
        'SCHOOL_TEST_POSTGRES_PASSWORD': database['POSTGRES_PASSWORD'],
        'STUDENT_CONCURRENCY_HTTP_PORT': str(manifest['port']),
        'SCHOOL_BACKEND_IMAGE': manifest['backend_image']}
    command = ['docker', 'compose', '-p', manifest['project']]
    for filename in ('docker-compose.yml', 'docker-compose.school.yml', 'docker-compose.school-test.yml',
                     'docker-compose.student-concurrency.yml', 'docker-compose.student-entry-sharing.yml',
                     'docker-compose.student-capacity.yml'):
        command += ['-f', str(source / 'deploy' / filename)]
    rendered = json.loads(call(command + ['config', '--format', 'json'], env=chosen))
    rendered = rebind_retest_compose(rendered, manifest, manifest['backend_image'], manifest['frontend_image'])
    expected = {'db', 'pgbouncer', 'migrate', 'web', 'entry', 'assessment', 'capture',
                'teacher-read', 'scheduler', 'analytics-worker', 'session-submission-worker', 'nginx'}
    active = {name: service for name, service in rendered['services'].items() if not service.get('profiles')}
    if set(active) != expected:
        raise ValueError('Unexpected active retest service set')
    rendered['services'] = active
    for service in active.values():
        for dependency, policy in list(service.get('depends_on', {}).items()):
            if dependency not in active:
                if policy.get('required', True):
                    raise ValueError('Missing required retest dependency')
                del service['depends_on'][dependency]
    private(root / 'private/compose-R0.json', json.dumps(rendered, indent=2))
    private(root / 'private/run-manifest.json', json.dumps(manifest, indent=2))
    call(['docker', 'compose', '-p', manifest['project'], '-f', str(root / 'private/compose-R0.json'), 'config', '--quiet'])
    report = {'state': 'prepared', 'project': manifest['project'], 'source_archive_sha256': archive_sha256,
              'compose_sha256': hashlib.sha256((root / 'private/compose-R0.json').read_bytes()).hexdigest(),
              'parameters': parameters}
    private(root / 'evidence/compose-preflight.json', json.dumps(report, indent=2))
    print(json.dumps({'state': 'prepared', 'project': manifest['project']}))


def variants():
    base = {'ASSESSMENT_ENTRY_WORKERS': '15', 'ASSESSMENT_ENTRY_DB_CONNECTIONS': '11',
            'ASSESSMENT_ENTRY_LOGIN_LIMIT': '12', 'ASSESSMENT_ENTRY_HEAVY_LIMIT': '14',
            'ASSESSMENT_ENTRY_STARTUP_LIMIT': '8', 'ASSESSMENT_ADMISSION_CAPACITY': '8',
            'ASSESSMENT_REMAINING_WORKERS': '4', 'ASSESSMENT_REMAINING_DB_CONNECTIONS': '4',
            'ASSESSMENT_SUBMISSION_WORKERS': '1', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '1',
            'ASSESSMENT_ANALYTICS_WORKERS': '1', 'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '2',
            'ASSESSMENT_ENTRY_STARTUP_BURST': '8', 'SCHOOL_HTTP_CONN_MAX_AGE': '0'}
    rows = [('B', base)]
    for name, changes in (
        ('W8D4', {'ASSESSMENT_REMAINING_WORKERS': '8'}),
        ('W8D8', {'ASSESSMENT_REMAINING_DB_CONNECTIONS': '8'}),
        ('W12D8', {'ASSESSMENT_REMAINING_WORKERS': '12'}),
        ('W12D12', {'ASSESSMENT_REMAINING_DB_CONNECTIONS': '12'}),
        ('Q2', {'ASSESSMENT_SUBMISSION_WORKERS': '2', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '2'}),
        ('Q4', {'ASSESSMENT_SUBMISSION_WORKERS': '4', 'ASSESSMENT_SUBMISSION_DB_CONNECTIONS': '4'}),
        ('A10', {'ASSESSMENT_ENTRY_STARTUP_LIMIT': '10', 'ASSESSMENT_ADMISSION_CAPACITY': '10'}),
        ('BURST32', {'ASSESSMENT_ENTRY_STARTUP_BURST': '32'}),
        ('E19', {'ASSESSMENT_ENTRY_WORKERS': '19', 'ASSESSMENT_ENTRY_DB_CONNECTIONS': '15',
                 'ASSESSMENT_ENTRY_HEAVY_LIMIT': '18', 'ASSESSMENT_ENTRY_LOGIN_LIMIT': '16',
                 'ASSESSMENT_ENTRY_STARTUP_LIMIT': '12', 'ASSESSMENT_ADMISSION_CAPACITY': '12'}),
        ('REUSE60', {'SCHOOL_HTTP_CONN_MAX_AGE': '60'}),
        ('STATS2', {'ASSESSMENT_ANALYTICS_WORKERS': '2', 'ASSESSMENT_BACKGROUND_DB_CONNECTIONS': '4'}),
    ):
        base = base | changes
        rows.append((name, base))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--run-manifest')
    args = parser.parse_args()
    if args.run_manifest:
        prepare_retest(args.archive, args.sha256, args.run_manifest)
        return
    archive = Path(args.archive).resolve()
    assert os.geteuid() == 0 and not ROOT.exists()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == args.sha256
    assert not call(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT]).strip()
    old = json.loads(Path('/home/ktdata/student-entry-sharing-tests/20261015/private/compose-S24.json').read_text())
    assert old['name'] == 'kt-school-e2e-entry-20261015'
    ROOT.mkdir(parents=True, mode=0o700)
    for name in ('source', 'private', 'evidence', 'ops'):
        (ROOT / name).mkdir(mode=0o700)
    source = ROOT / 'source'
    with tarfile.open(archive) as stream:
        assert all(not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in stream.getmembers())
        stream.extractall(source, filter='data')
    environment = copy.deepcopy(old['services']['web']['environment'])
    db = old['services']['db']['environment']
    environment.update(POSTGRES_DB='kt_e2e', POSTGRES_USER=db['POSTGRES_USER'], POSTGRES_PASSWORD=db['POSTGRES_PASSWORD'],
        POSTGRES_HOST='pgbouncer', POSTGRES_PORT='5432', SCHOOL_RUNTIME_ISOLATION_ENABLED='1',
        SCHOOL_REDIS_SESSION_ENABLED='0', DJANGO_ALLOWED_HOSTS='localhost,127.0.0.1',
        DJANGO_CSRF_TRUSTED_ORIGINS=f'http://127.0.0.1:{PORT},http://localhost:{PORT},http://127.0.0.1:18082',
        DJANGO_SECURE_SSL_REDIRECT='0', DJANGO_SESSION_COOKIE_SECURE='0', DJANGO_CSRF_COOKIE_SECURE='0',
        ASSESSMENT_PREPARED_ENABLED='1', ASSESSMENT_TRANSITION_ENABLED='1',
        ASSESSMENT_TRANSITION_COURSE_IDS='*', CLOSE_BUNDLE_ENABLED='1',
        ASSESSMENT_ADMISSION_ENABLED='1', ASSESSMENT_ADMISSION_GLOBAL_ENABLED='1',
        ASSESSMENT_PREPARED_ADMISSION_MODE='postgres_transaction', RUN_SCHEDULER_EXPORT_JOBS='0')
    text = ''.join(key + '=' + json.dumps(str(value)) + '\n' for key, value in environment.items())
    private(source / 'server/.env', text)
    private(source / 'deploy/school-test.env', text)
    files = ['docker-compose.yml', 'docker-compose.school.yml', 'docker-compose.school-test.yml',
             'docker-compose.student-concurrency.yml', 'docker-compose.student-entry-sharing.yml',
             'docker-compose.student-capacity.yml']
    records = []
    for candidate, variant in variants():
        chosen = dict(os.environ, **environment) | variant | {
            'SCHOOL_TEST_POSTGRES_USER': db['POSTGRES_USER'], 'SCHOOL_TEST_POSTGRES_PASSWORD': db['POSTGRES_PASSWORD'],
            'SCHOOL_BACKEND_IMAGE': 'kt-student-capacity-backend:20261016-v1',
            'STUDENT_CONCURRENCY_HTTP_PORT': str(PORT)}
        command = ['docker', 'compose', '-p', PROJECT]
        for name in files:
            command += ['-f', str(source / 'deploy' / name)]
        config = json.loads(call(command + ['config', '--format', 'json'], env=chosen))
        assert config['name'] == PROJECT
        for kind in ('volumes', 'networks'):
            for name, item in config.get(kind, {}).items():
                assert not item.get('external')
                item['name'] = PROJECT + '_' + name
        active = {name: service for name, service in config['services'].items() if not service.get('profiles')}
        assert set(active) == {'db', 'pgbouncer', 'migrate', 'web', 'entry', 'assessment', 'capture',
                               'teacher-read', 'scheduler', 'analytics-worker', 'session-submission-worker', 'nginx'}
        for name, service in active.items():
            service.pop('build', None)
            service.pop('container_name', None)
            if name == 'nginx':
                service['image'] = 'kt-student-capacity-frontend:20261016-v1'
            elif name not in ('db', 'pgbouncer'):
                service['image'] = 'kt-student-capacity-backend:20261016-v1'
                service['environment']['DJANGO_CSRF_TRUSTED_ORIGINS'] = environment['DJANGO_CSRF_TRUSTED_ORIGINS']
            for mount in service.get('volumes', []):
                if mount['type'] == 'bind':
                    assert Path(mount['source']).resolve().is_relative_to(source)
        for service in config['services'].values():
            for dependency, policy in list(service.get('depends_on', {}).items()):
                if dependency not in config['services']:
                    assert policy.get('required') is False, 'Required dependency is absent'
                    del service['depends_on'][dependency]
        assert active['db']['environment']['POSTGRES_DB'] == 'kt_e2e'
        path = ROOT / f'private/compose-{candidate}.json'
        private(path, json.dumps(config, indent=2))
        call(['docker', 'compose', '-p', PROJECT, '-f', str(path), 'config', '--quiet'])
        records.append({'candidate': candidate, 'parameters': variant, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    (ROOT / 'evidence/compose-preflight.json').write_text(json.dumps(records, indent=2))
    (ROOT / 'evidence/source-archive.json').write_text(json.dumps({'sha256': args.sha256, 'archive': str(archive)}))
    print(json.dumps({'state': 'prepared', 'project': PROJECT, 'candidates': len(records)}))


if __name__ == '__main__':
    main()
