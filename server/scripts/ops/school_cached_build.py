#!/usr/bin/env python3
"""Guarded offline builder and owned pre-build-only resume. No secret output."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from school_release_support import extract_verified_source, file_sha256


WEB_ID = 'sha256:8ffceed6f076328ad9fff93dc063480ef4b6db6c7ad9c7a1a72fa3c07889d080'
NGINX_ID = 'sha256:2204ebd593576661f1e504976850bc4d4614e81a87038bec6009532095be68f7'
PROJECT = 'kt-school-e2e'
CANDIDATE = Path('/home/ktdata/school-cache-candidate')
RETAINED_TEST_DB = 'kt_e2e_v4_20261010_115729'


def owned_test_reset_allowed(owner, comment, run_id, retained_exists, running_test_containers):
    return owner == 'kt_e2e_user' and comment == 'school-cache-candidate ' + run_id and not retained_exists and not running_test_containers


def docker(*args, **kwargs):
    return subprocess.check_output(['docker', *args], text=True, **kwargs).strip()


def digest(path):
    return file_sha256(path)


def read_environment(path):
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise ValueError('Test environment must remain root-private')
    values = dict(line.split('=', 1) for line in path.read_text().splitlines() if line)
    if values.get('POSTGRES_DB') != 'kt_e2e' or values.get('POSTGRES_USER') != 'kt_e2e_user':
        raise ValueError('Unexpected test database or user')
    for key, value in values.items():
        if not re.fullmatch(r'[A-Z0-9_]+', key) or not re.fullmatch(r'[A-Za-z0-9_.,:/*-]+', value):
            raise ValueError('Unsafe test environment value')
    if values['POSTGRES_PASSWORD'] != values['SCHOOL_TEST_POSTGRES_PASSWORD'] or len(values['DJANGO_SECRET_KEY']) < 64:
        raise ValueError('Unexpected private test credentials')
    return values


def safe_failure_message(error):
    if not isinstance(error, ValueError):
        return type(error).__name__
    # These are our constant guard diagnostics; never echo arbitrary parser,
    # Docker, database, environment-value, or SQL exception strings.
    allowed = ('Test environment must remain root-private', 'Unexpected test database or user',
        'Unsafe test environment value', 'Unexpected private test credentials',
        'Expected root and physical candidate directory', 'Unexpected owned run ID',
        'Resume only a known failed pre-build stage', 'Resume requires no fixtures or test containers',
        'Test environment link does not match owned run', 'Uploaded archive hash mismatch',
        'Repeated resume requires exactly the same validated source', 'Production web is ambiguous',
        'Verified cached image missing', 'Cached Python/Django/dependency ABI mismatch',
        'Unknown frontend directory: refusing replacement', 'Unsafe frontend archive; expected spa-assets root',
        'Existing frontend differs from bound upload', 'Frontend upload is incomplete',
        'Cached build binding mismatch', 'Local base tag mismatch', 'Candidate archive SHA256 mismatch',
        'Unsafe archive entry', 'Archive must contain exactly one school source root',
        'Archive contains files outside source root', 'Tested candidate differs from accepted archive')
    text = str(error)
    if text in allowed:
        return text
    if text.startswith('Tested candidate differs from accepted archive: '):
        return 'Tested candidate differs from accepted archive; inspect source binding'
    if text.startswith('Offline build failed for target ') and text.rsplit(' ', 1)[-1] in ('web', 'scheduler', 'analytics-worker', 'publication-worker', 'student-comparison-worker', 'export-worker', 'nginx-school'):
        return text
    return type(error).__name__


def verify_resume(args):
    if os.geteuid() != 0 or CANDIDATE.resolve() != CANDIDATE:
        raise ValueError('Expected root and physical candidate directory')
    if not re.fullmatch(r'\d{8}T\d{6}Z-\d+', args.run_id):
        raise ValueError('Unexpected owned run ID')
    private = Path('/home/ktdata/school-cache-private') / args.run_id
    public = Path('/home/ktdata/school-cache-reports') / args.run_id
    status = json.loads((public / 'status.json').read_text())
    upgrading = args.allow_owned_test_reseed_upgrade
    if upgrading and args.run_id != '20261010T115729Z-358195':
        raise ValueError('Owned reset is restricted to the explicitly identified v4 run')
    if status.get('run_id') != args.run_id or status.get('state') != 'failed' or (not upgrading and status.get('stage') not in ('candidate-build', 'cached-candidate-build')):
        raise ValueError('Resume only a known failed pre-build stage')
    if not upgrading and ((private / 'fixtures').exists() or docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT)):
        raise ValueError('Resume requires no fixtures or test containers')
    if upgrading and docker('ps', '-q', '--filter', 'label=com.docker.compose.project=' + PROJECT):
        raise ValueError('Owned test upgrade requires every test container stopped')
    values = read_environment(private / 'school-test.env')
    link = CANDIDATE / 'school-test.env'
    if not link.is_symlink() or link.resolve() != (private / 'school-test.env').resolve():
        raise ValueError('Test environment link does not match owned run')
    if digest(args.archive) != args.sha256 or digest(args.frontend_archive) != args.frontend_sha256:
        raise ValueError('Uploaded archive hash mismatch')
    binding = {'candidate_archive_sha256': args.sha256, 'frontend_archive_sha256': args.frontend_sha256,
               'web_base_image': WEB_ID, 'nginx_base_image': NGINX_ID, 'run_id': args.run_id}
    binding_path = private / 'cached-source-binding.json'
    old_binding = json.loads(binding_path.read_text()) if binding_path.exists() else None
    if upgrading and (not old_binding or old_binding['frontend_archive_sha256'] != args.frontend_sha256):
        raise ValueError('Owned source upgrade requires the previously bound frontend')
    if old_binding and old_binding != binding and not upgrading:
        raise ValueError('Repeated resume requires exactly the same validated source')
    with tempfile.TemporaryDirectory(prefix='school-source-verify-', dir=private) as temporary:
        extract_verified_source(args.archive, args.sha256, CANDIDATE, Path(temporary) / 'verified')
    web = docker('ps', '-q', '--filter', 'label=com.docker.compose.project=kt_plantform', '--filter', 'label=com.docker.compose.service=web').split()
    if len(web) != 1:
        raise ValueError('Production web is ambiguous')
    script = '''
import json,os,sys,psycopg
values=json.load(sys.stdin)
with psycopg.connect(host='db',port=5432,dbname=os.environ['POSTGRES_DB'],user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],connect_timeout=5,options='-c statement_timeout=30000') as conn:
 with conn.cursor() as cur:
  cur.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname='kt_e2e'")
  assert cur.fetchone()==('kt_e2e_user','school-cache-candidate '+values['run_id'])
  cur.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='kt_e2e_v4_20261010_115729')")
  assert not values['upgrading'] or not cur.fetchone()[0]
  cur.execute("SELECT rolconnlimit,rolcanlogin,rolsuper FROM pg_roles WHERE rolname='kt_e2e_user'")
  assert cur.fetchone()==(48,True,False)
with psycopg.connect(host='db',port=5432,dbname='kt_e2e',user='kt_e2e_user',password=values['POSTGRES_PASSWORD'],connect_timeout=5) as conn:
 with conn.cursor() as cur:
  cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
  count=cur.fetchone()[0]
  assert values['upgrading'] or count==0
print('owned-test-database-verified')
'''
    docker('exec', '-i', web[0], 'python', '-c', script,
           input=json.dumps({'run_id': args.run_id, 'upgrading': upgrading, 'POSTGRES_PASSWORD': values['POSTGRES_PASSWORD']}), stderr=subprocess.DEVNULL)
    for image in (WEB_ID, NGINX_ID):
        if docker('image', 'inspect', image, '--format', '{{.Id}}') != image:
            raise ValueError('Verified cached image missing')
    runtime = json.loads(docker('run', '--rm', '--pull=never', '--entrypoint', 'python', WEB_ID, '-c',
        "import sys,django,hashlib,json;p=open('/app/requirements.txt','rb').read();print(json.dumps({'python':list(sys.version_info[:2]),'django':django.get_version(),'requirements_raw_sha256':hashlib.sha256(p).hexdigest(),'requirements_lf_sha256':hashlib.sha256(p.replace(bytes([13,10]),bytes([10]))).hexdigest()}))"))
    candidate_requirements = (CANDIDATE / 'requirements.txt').read_bytes()
    candidate_lf_sha = hashlib.sha256(candidate_requirements.replace(b'\r\n', b'\n')).hexdigest()
    runtime['candidate_requirements_raw_sha256'] = hashlib.sha256(candidate_requirements).hexdigest()
    if runtime['python'] != [3, 13] or runtime['django'] != '5.2.16' or runtime['requirements_lf_sha256'] != candidate_lf_sha:
        raise ValueError('Cached Python/Django/dependency ABI mismatch')
    destination = CANDIDATE / '.school-frontend'
    if destination.exists():
        # Only a matching prior resume may reuse the exact uploaded frontend.
        if not binding_path.exists():
            raise ValueError('Unknown frontend directory: refusing replacement')
    else:
        destination.mkdir(mode=0o755)
    with tarfile.open(args.frontend_archive) as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or '..' in path.parts or entry.issym() or entry.islnk() or not path.parts or path.parts[0] != 'spa-assets':
                raise ValueError('Unsafe frontend archive; expected spa-assets root')
            if entry.isfile():
                target = destination.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.extractfile(entry).read()
                if target.exists() and target.read_bytes() != content:
                    raise ValueError('Existing frontend differs from bound upload')
                target.write_bytes(content)
                target.chmod(0o644)
    if not (destination / 'spa-assets/index.html').is_file() or not (destination / 'spa-assets/assets').is_dir():
        raise ValueError('Frontend upload is incomplete')
    destination.chmod(0o755)
    for directory in destination.rglob('*'):
        if directory.is_dir():
            directory.chmod(0o755)
    if upgrading:
        journal = private / ('retained-v4-' + str(time.time_ns()))
        journal.mkdir(mode=0o700)
        (journal / 'source-binding.json').write_text(json.dumps(old_binding, indent=2))
        for name in ('fixtures', 'fixture-password'):
            path = private / name
            if path.exists():
                shutil.move(str(path), str(journal / name))
        shutil.copytree(public, journal / 'public-evidence')
        stopped_ids = docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT).split()
        if stopped_ids:
            stopped = json.loads(docker('inspect', *stopped_ids))
            if any(item['State']['Running'] or item['Config']['Labels'].get('com.docker.compose.project') != PROJECT
                   or item['Config']['Labels'].get('com.docker.compose.service') == 'db' for item in stopped):
                raise ValueError('Owned test upgrade found an unexpected test container')
            subprocess.run(['docker', 'rm', *stopped_ids], check=True, stdout=subprocess.DEVNULL)
        # Explicitly authorized isolated-test archive, never production DDL or
        # deletion. Keep the old physical DB recoverable under its fixed name.
        reset_script = '''
import json,os,sys,psycopg
from psycopg import sql
values=json.load(sys.stdin)
with psycopg.connect(host='db',port=5432,dbname=os.environ['POSTGRES_DB'],user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],connect_timeout=5,autocommit=True,options='-c statement_timeout=30000 -c lock_timeout=5000') as conn:
 with conn.cursor() as cur:
  cur.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname='kt_e2e'")
  assert cur.fetchone()==('kt_e2e_user','school-cache-candidate '+values['run_id'])
  cur.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='kt_e2e_v4_20261010_115729')")
  assert not cur.fetchone()[0]
  cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='kt_e2e' AND usename='kt_e2e_user' AND pid<>pg_backend_pid()")
  cur.execute('ALTER DATABASE kt_e2e RENAME TO kt_e2e_v4_20261010_115729')
  cur.execute('CREATE DATABASE kt_e2e OWNER kt_e2e_user')
  cur.execute(sql.SQL('COMMENT ON DATABASE kt_e2e IS {}').format(sql.Literal('school-cache-candidate '+values['run_id'])))
print('owned-v4-test-database-retained-and-fresh-v5-created')
'''
        docker('exec', '-i', web[0], 'python', '-c', reset_script, input=json.dumps({'run_id': args.run_id}), stderr=subprocess.DEVNULL)
        (public / 'retained-test-db.json').write_text(json.dumps({'retained_database': RETAINED_TEST_DB,
            'new_database': 'kt_e2e', 'production_database_mutated': False, 'old_test_data_deleted': False}, indent=2))
        (public / 'retained-test-db.json').chmod(0o644)
    binding_path.write_text(json.dumps(binding, indent=2))
    binding_path.chmod(0o600)
    (public / 'cached-build-provenance.json').write_text(json.dumps({**binding, 'runtime': runtime, 'builder': 'legacy-offline', 'dependency_install_performed': False}, indent=2))
    (public / 'cached-build-provenance.json').chmod(0o644)
    print('owned-resume-source-and-runtime-verified')


def build(args):
    private = Path('/home/ktdata/school-cache-private') / args.run_id
    binding = json.loads((private / 'cached-source-binding.json').read_text())
    if binding['run_id'] != args.run_id:
        raise ValueError('Cached build binding mismatch')
    bases = {'web': 'kt-school-cached-web:' + WEB_ID[7:19], 'nginx': 'kt-school-cached-nginx:' + NGINX_ID[7:19]}
    for name, image in (('web', WEB_ID), ('nginx', NGINX_ID)):
        subprocess.run(['docker', 'tag', image, bases[name]], check=True)
        if docker('image', 'inspect', bases[name], '--format', '{{.Id}}') != image:
            raise ValueError('Local base tag mismatch')
    targets = {'web': 'web', 'student-read': 'web', 'teacher-read': 'web', 'migrate': 'web',
               'scheduler': 'scheduler', 'analytics-worker': 'analytics-worker',
               'publication-worker': 'publication-worker', 'student-comparison-worker': 'student-comparison-worker',
               'export-worker': 'export-worker', 'nginx': 'nginx-school'}
    definitions, images = {}, {}
    built = {}
    for service, target in targets.items():
        tag = PROJECT + '-cached-' + target + ':' + args.run_id
        if target not in built:
            command = ['docker', 'build', '--pull=false', '-f', 'Dockerfile.school-cached', '--target', target,
                            '--build-arg', 'SCHOOL_CACHED_WEB_BASE=' + bases['web'], '--build-arg', 'SCHOOL_CACHED_NGINX_BASE=' + bases['nginx'],
                            '-t', tag, '.']
            log = private / ('build-' + target + '.log')
            print('offline-building-target ' + target, flush=True)
            with log.open('w') as output:
                completed = subprocess.run(command, cwd=CANDIDATE, env={**os.environ, 'DOCKER_BUILDKIT': '0'}, stdout=output, stderr=subprocess.STDOUT)
            if completed.returncode:
                lines = log.read_text(errors='replace').splitlines()
                selected = [line for line in lines if re.search(r'ERROR|Error|error:|not found|returned a non-zero|ModuleNotFoundError|SyntaxError|Exception|timeout', line)][-12:]
                secrets = [value for key, value in read_environment(private / 'school-test.env').items() if 'PASSWORD' in key or 'SECRET' in key]
                diagnostic = '\n'.join(selected)[-4000:]
                for secret in secrets:
                    diagnostic = diagnostic.replace(secret, '[redacted]')
                diagnostic = re.sub(r'(https?://)[^ /]+@', r'\1[redacted]@', diagnostic)
                error_path = Path('/home/ktdata/school-cache-reports') / args.run_id / 'build-error.json'
                error_path.write_text(json.dumps({'stage': 'cached-candidate-build', 'target': target,
                    'exit_code': completed.returncode, 'diagnostic': diagnostic or 'See root-private build log'}, indent=2))
                error_path.chmod(0o644)
                raise ValueError('Offline build failed for target ' + target)
            built[target] = docker('image', 'inspect', tag, '--format', '{{.Id}}')
        images[service] = built[target]
        definitions[service] = {'image': tag, 'pull_policy': 'never'}
    # PgBouncer itself must also be available offline; never silently pull.
    docker('image', 'inspect', 'edoburu/pgbouncer:v1.24.1-p1', '--format', '{{.Id}}')
    definitions['pgbouncer'] = {'pull_policy': 'never'}
    (private / 'cached-images.json').write_text(json.dumps({'services': definitions}, indent=2))
    (private / 'cached-images.json').chmod(0o600)
    public = Path('/home/ktdata/school-cache-reports') / args.run_id
    (public / 'candidate-image-ids.json').write_text(json.dumps(images, indent=2))
    (public / 'candidate-image-ids.json').chmod(0o644)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('verify-resume', 'build'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--allow-owned-test-reseed-upgrade', action='store_true')
    for name in ('archive', 'sha256', 'frontend-archive', 'frontend-sha256'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    try:
        verify_resume(args) if args.mode == 'verify-resume' else build(args)
    except Exception as error:
        print('Cached build/resume failed: ' + safe_failure_message(error))
        raise SystemExit(1)
