"""Build immutable test images and start the explicitly owned capacity project."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from prepare_student_capacity_remote import ROOT, PROJECT
from capacity_retest import HTTP_WORKERS, validate_manifest, r0_parameters


def call(args, log=None, timeout=600):
    if log:
        with log.open('x') as stream:
            subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=timeout)
        return ''
    return subprocess.check_output(args, text=True, timeout=timeout)


def others():
    ids = call(['docker', 'ps', '-aq']).split()
    rows = json.loads(call(['docker', 'inspect', *ids])) if ids else []
    return [{'id': row['Id'], 'name': row['Name'], 'image': row['Image'], 'running': row['State']['Running'],
             'started_at': row['State']['StartedAt'], 'restarts': row['RestartCount']}
            for row in rows if row['Config'].get('Labels', {}).get('com.docker.compose.project') != PROJECT]


def image_resume_allowed(image_id, state, role, context_sha256):
    return state.get('context_sha256') == context_sha256 and state.get(role + '_image_id') == image_id


def validate_r0_config(config, manifest):
    values = r0_parameters() | manifest.get('r0', {})
    for role, key in HTTP_WORKERS.items():
        environment = config['services'][role]['environment']
        if (str(environment.get('GUNICORN_WORKERS')) != str(values[key])
                or environment.get('SCHOOL_POOL_PROFILE') != 'shared-entry-v3'
                or str(environment.get('SCHOOL_REDIS_SESSION_ENABLED')) != '0'):
            raise ValueError('Prepared R0 worker/profile/session configuration differs: ' + role)


def local_base_reference(role, image_id):
    return 'kt-capacity-base-' + role + ':' + image_id.removeprefix('sha256:')


def pin_local_bases(bases):
    for role, image_id in bases.items():
        reference = local_base_reference(role, image_id)
        call(['docker', 'image', 'tag', image_id, reference])
        if call(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference]).strip() != image_id:
            raise ValueError('Local base image tag differs from the frozen image ID')


def write_retest_dockerfiles(source, bases):
    (source / '.dockerignore').write_text('server/.env\ndeploy/school-test.env\nserver/media/\n'
        'server/staticfiles/\nserver/**/__pycache__/\nserver/.venv/\nserver/db.sqlite3\n')
    (source / 'Dockerfile.capacity-backend').write_text(
        f'FROM {local_base_reference("backend", bases["backend"])}\nCOPY server/ /app/\nCOPY deploy/docker/ /app/docker/\n'
        'RUN sed -i "s/\\r$//" /app/docker/*.sh && chmod +x /app/docker/*.sh\n')
    (source / 'Dockerfile.capacity-frontend').write_text(
        f'FROM {local_base_reference("frontend", bases["frontend"])}\nCOPY client/dist/spa-assets/ /static/spa-assets/\n'
        'COPY client/dist/spa-assets/assets/ /opt/kt-frontend-assets/\n'
        'COPY client/dist/spa-assets/capture-shell-*.html /opt/kt-frontend-shells/\n'
        'COPY client/legacy/js/ /static/js/\n'
        'COPY deploy/docker/20-frontend-assets.sh /docker-entrypoint.d/20-frontend-assets.sh\n'
        'RUN sed -i "s/\\r$//" /docker-entrypoint.d/20-frontend-assets.sh && chmod +x /docker-entrypoint.d/20-frontend-assets.sh\n')


def _context_digest(source):
    value = hashlib.sha256()
    for directory in ('server', 'deploy/docker', 'client/dist/spa-assets', 'client/legacy/js'):
        base = source / directory
        if not base.is_dir():
            raise ValueError('Current build source missing: ' + directory)
        for path in sorted(base.rglob('*')):
            relative = path.relative_to(source)
            if (set(relative.parts) & {'__pycache__', '.venv'} or relative.as_posix() in ('server/.env', 'server/db.sqlite3')
                    or relative.is_relative_to(Path('server/media')) or relative.is_relative_to(Path('server/staticfiles'))):
                continue
            if path.is_symlink():
                raise ValueError('Build source symlinks rejected')
            if path.is_file():
                value.update(relative.as_posix().encode() + b'\0')
                with path.open('rb') as stream:
                    value.update(hashlib.file_digest(stream, 'sha256').digest())
    return value.hexdigest()


def build_retest(manifest):
    from student_capacity_retest_store import SnapshotStore
    manifest = validate_manifest(manifest)
    root, project = Path(manifest['root']), manifest['project']
    source = root / 'source'
    preflight = json.loads((root / 'evidence/compose-preflight.json').read_text())
    source_sha = manifest.get('build_source_sha256', manifest['source_archive_sha256'])
    if preflight.get('project') != project or preflight.get('source_archive_sha256') != source_sha:
        raise ValueError('Prepared source archive hash/project differs from build manifest')
    path = root / 'private/retest-build.json'
    prior = json.loads(path.read_text()) if path.exists() else {}
    context = _context_digest(source)
    bases = {role: call(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]).strip()
             for role, tag in (('backend', 'kt-student-capacity-backend:20261016-v6'),
                               ('frontend', 'kt-student-capacity-frontend:20261016-v5'))}
    if prior and (prior.get('project') != project or prior.get('context_sha256') != context
                  or prior.get('source_archive_sha256') != source_sha or prior.get('base_image_ids') != bases):
        raise ValueError('Existing build artifact does not prove the same frozen source and base images')
    tags = {role: manifest[role + '_image'] for role in bases}
    if len(set(tags.values())) != 2 or any(tag in ('kt-student-capacity-backend:20261016-v6',
            'kt-student-capacity-frontend:20261016-v5') for tag in tags.values()):
        raise ValueError('New retest image tags must be distinct from installed baselines')
    # Refuse an unproven existing target before changing build artifacts or starting containers.
    installed = {}
    for role, tag in tags.items():
        probe = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            installed[role] = probe.stdout.strip()
            if not image_resume_allowed(installed[role], prior, role, context):
                raise ValueError('Existing image tag lacks same-source resume evidence: ' + tag)
    state = prior | {'state': 'building', 'project': project, 'source_archive_sha256': source_sha,
        'context_sha256': context, 'base_image_ids': bases, 'image_tags': tags, 'started_epoch': time.time()}
    def save():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)
    save()
    pin_local_bases(bases)
    write_retest_dockerfiles(source, bases)
    try:
        for role, tag in tags.items():
            if role not in installed:
                reference = local_base_reference(role, bases[role])
                if call(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference]).strip() != bases[role]:
                    raise ValueError('Local base image changed before build')
                log = root / f'private/build-{role}-{time.time_ns()}.log'
                call(['docker', 'build', '--pull=false', '-f', str(source / f'Dockerfile.capacity-{role}'),
                      '-t', tag, str(source)], log, timeout=600)
                if call(['docker', 'image', 'inspect', '--format', '{{.Id}}', reference]).strip() != bases[role]:
                    raise ValueError('Local base image changed during build')
                log.chmod(0o600)
            state[role + '_image_id'] = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]).strip()
            save()
        config = root / 'private/compose-R0.json'
        store = SnapshotStore(manifest, config)
        parsed = store._load_config(config)
        validate_r0_config(parsed, manifest)
        store.preflight_resources(parsed)
        for role, service in parsed['services'].items():
            expected = tags['frontend'] if role == 'nginx' else tags['backend']
            if role not in ('db', 'pgbouncer') and service.get('image') != expected:
                raise ValueError('Compose does not use the frozen retest images')
        web = store._start(config)
        state['postgres_actual'] = store._db_ready(config, parsed)
        rows = store._inspect_owned()
        for row in rows:
            role = row['Config']['Labels'].get('com.docker.compose.service')
            if role in ('db', 'pgbouncer', 'nginx'):
                continue
            env = dict(value.split('=', 1) for value in row['Config']['Env'] if '=' in value)
            expected = parsed['services'][role].get('environment', {})
            for key in ('SCHOOL_POOL_PROFILE', 'SCHOOL_REDIS_SESSION_ENABLED', 'GUNICORN_WORKERS'):
                if key in expected and env.get(key) != str(expected[key]):
                    raise ValueError('Actual retest runtime differs: ' + role + '/' + key)
        state.update(state='ready', candidate='R0', web_container_id=web,
            private_compose_sha256=hashlib.sha256(config.read_bytes()).hexdigest(), ended_epoch=time.time())
    except Exception as error:
        state.update(state='failed', error_type=type(error).__name__, ended_epoch=time.time())
        raise
    finally:
        save()
    print(json.dumps(state))
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-manifest')
    args = parser.parse_args()
    manifest_path = args.run_manifest or os.getenv('SCHOOL_CAPACITY_RUN_MANIFEST')
    if manifest_path:
        return build_retest(json.loads(Path(manifest_path).read_text()))
    assert (ROOT / 'evidence/compose-preflight.json').exists()
    assert not (ROOT / 'evidence/build-status.json').exists()
    assert not call(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + PROJECT]).strip()
    source = ROOT / 'source'
    state = {'state': 'building', 'started_epoch': time.time()}
    status = ROOT / 'evidence/build-status.json'
    def save():
        status.write_text(json.dumps(state, indent=2))
    save()
    (ROOT / 'evidence/other-containers-before.json').write_text(json.dumps(others(), indent=2))
    backend_base = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'kt-entry-sharing-backend:20261015-v2']).strip()
    frontend_base = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'kt-entry-sharing-frontend:20261015-v1']).strip()
    state.update(backend_base_image_id=backend_base, frontend_base_image_id=frontend_base)
    (source / '.dockerignore').write_text('server/.env\ndeploy/school-test.env\n')
    (source / 'Dockerfile.capacity-backend').write_text(
        'FROM kt-entry-sharing-backend:20261015-v2\nCOPY server/ /app/\nCOPY deploy/docker/ /app/docker/\n'
        'RUN sed -i "s/\\r$//" /app/docker/*.sh && chmod +x /app/docker/*.sh\n')
    (source / 'Dockerfile.capacity-frontend').write_text(
        'FROM kt-entry-sharing-frontend:20261015-v1\nCOPY client/dist/spa-assets/ /static/spa-assets/\n'
        'COPY client/dist/spa-assets/assets/ /opt/kt-frontend-assets/\n'
        'COPY client/dist/spa-assets/capture-shell-*.html /opt/kt-frontend-shells/\n'
        'COPY client/legacy/js/ /static/js/\n'
        'COPY deploy/docker/20-frontend-assets.sh /docker-entrypoint.d/20-frontend-assets.sh\n'
        'RUN sed -i "s/\\r$//" /docker-entrypoint.d/20-frontend-assets.sh && chmod +x /docker-entrypoint.d/20-frontend-assets.sh\n')
    try:
        for role in ('backend', 'frontend'):
            tag = f'kt-student-capacity-{role}:20261016-v1'
            probe = subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True)
            assert probe.returncode != 0, 'Image tag already exists'
            call(['docker', 'build', '--pull=false', '-f', str(source / f'Dockerfile.capacity-{role}'),
                  '-t', tag, str(source)], ROOT / f'evidence/build-{role}.log')
            state[role + '_image_id'] = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag]).strip()
            save()
        config = ROOT / 'private/compose.json'
        shutil.copyfile(ROOT / 'private/compose-B.json', config)
        command = ['docker', 'compose', '-p', PROJECT, '-f', str(config)]
        call(command + ['up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '180'], ROOT / 'evidence/startup-B.log', timeout=240)
        call(command + ['exec', '-T', 'nginx', 'nginx', '-t'], ROOT / 'evidence/nginx-B-check.log')
        state.update(state='ready', candidate='B', ended_epoch=time.time(),
                     private_compose_sha256=hashlib.sha256(config.read_bytes()).hexdigest())
    except Exception as error:
        state.update(state='failed', error_type=type(error).__name__, ended_epoch=time.time())
        raise
    finally:
        save()
    print(json.dumps(state))


if __name__ == '__main__':
    main()
