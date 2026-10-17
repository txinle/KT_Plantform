"""Verified logical snapshots of the new retest project; never touch original volumes."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

from capacity_retest import validate_manifest

ACTIVE_SERVICES = ('db', 'pgbouncer', 'migrate', 'web', 'entry', 'assessment', 'capture',
                   'teacher-read', 'scheduler', 'analytics-worker', 'session-submission-worker', 'nginx')


def call(args, *, stdin=None, stdout=None, timeout=600):
    result = subprocess.run(args, stdin=stdin, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE,
                            check=True, timeout=timeout)
    return result.stdout.decode() if result.stdout else ''


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_owned_compose(config, path, manifest):
    validate_manifest(manifest)
    root, chosen = PurePosixPath(manifest['root']), PurePosixPath(str(path).replace('\\', '/'))
    if config.get('name') != manifest['project'] or '..' in chosen.parts or not chosen.is_relative_to(root / 'private'):
        raise ValueError('Only new-project private compose files are permitted')
    if set(config.get('services', {})) != set(ACTIVE_SERVICES):
        raise ValueError('Exactly the twelve active retest services are required')
    for kind in ('volumes', 'networks'):
        for alias, value in config.get(kind, {}).items():
            if value.get('external') or value.get('name') != manifest['project'] + '_' + alias:
                raise ValueError('Foreign or external retest resources rejected')
    for role, service in config['services'].items():
        if service.get('profiles') or service.get('container_name'):
            raise ValueError('Inactive profiles and fixed container identities rejected')
        env = service.get('environment', {})
        if 'POSTGRES_DB' in env and env['POSTGRES_DB'] != 'kt_e2e':
            raise ValueError('Only kt_e2e may be restored')
        for port in service.get('ports', []):
            if (role != 'nginx' or port.get('host_ip') != '127.0.0.1'
                    or str(port.get('published')) != str(manifest['port']) or port.get('target') != 80
                    or port.get('protocol', 'tcp') != 'tcp'):
                raise ValueError('Only the authorized retest loopback port is permitted')
        for mount in service.get('volumes', []):
            if mount['type'] == 'bind':
                source = PurePosixPath(mount['source'])
                if '..' in source.parts or not source.is_relative_to(root / 'source'):
                    raise ValueError('Bind mount outside the new frozen source')
            elif mount['type'] != 'volume' or mount['source'] not in config.get('volumes', {}):
                raise ValueError('Undeclared retest volume rejected')
    if config['services']['db'].get('environment', {}).get('POSTGRES_DB') != 'kt_e2e':
        raise ValueError('The retest database must be kt_e2e')
    for role, alias, target in (('db', 'postgres_data', '/var/lib/postgresql/data'), ('web', 'media_data', '/app/media')):
        if not any(m.get('type') == 'volume' and m.get('source') == alias and m.get('target') == target
                   for m in config['services'][role].get('volumes', [])):
            raise ValueError('Retest database/media target differs')
    return config


def validate_volume_record(volume, alias, manifest, mounts=None):
    name = manifest['project'] + '_' + alias
    labels = volume.get('Labels') or {}
    point = PurePosixPath(volume['Mountpoint'])
    if (volume['Name'] != name or labels.get('com.docker.compose.project') != manifest['project']
            or labels.get('com.docker.compose.volume') != alias or point.name != '_data'
            or point.parent.name != name or '..' in point.parts
            or (mounts is not None and not any(m.get('Type') == 'volume' and m.get('Name') == name
                       and PurePosixPath(m['Source']) == point for m in mounts))):
        raise ValueError('Actual retest volume label/name/mount differs')
    resolved = Path(volume['Mountpoint']).resolve()
    if resolved.name != '_data' or resolved.parent.name != name:
        raise ValueError('Resolved volume mount escapes its owned data directory')
    return resolved


def tree_manifest(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Media root is absent or a symlink')
    result = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Media symlink rejected before snapshot or cleanup')
        stat = path.stat()
        result.append({'path': path.relative_to(root).as_posix(), 'directory': path.is_dir(),
            'size': stat.st_size if path.is_file() else 0, 'sha256': digest(path) if path.is_file() else None,
            'mode': stat.st_mode & 0o7777, 'uid': stat.st_uid, 'gid': stat.st_gid})
    return result


def _copy_media(source, target):
    tree_manifest(source)
    shutil.copytree(source, target, copy_function=shutil.copy2, dirs_exist_ok=True)
    for path in [source, *source.rglob('*')]:
        destination = target / path.relative_to(source)
        stat = path.stat()
        if hasattr(os, 'chown'):
            os.chown(destination, stat.st_uid, stat.st_gid)


class SnapshotStore:
    def __init__(self, manifest, compose_path, runner=call):
        self.manifest = validate_manifest(manifest)
        self.root = Path(manifest['root']).resolve()
        self.compose_path = Path(compose_path).resolve()
        self.call = runner
        self._load_config(self.compose_path)

    def _load_config(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root / 'private'):
            raise ValueError('Compose path must resolve inside the new private directory')
        config = json.loads(path.read_text())
        validate_owned_compose(config, path, self.manifest)
        for service in config['services'].values():
            for mount in service.get('volumes', []):
                if mount['type'] == 'bind' and not Path(mount['source']).resolve().is_relative_to(self.root / 'source'):
                    raise ValueError('Resolved bind mount escapes the frozen source')
        return config

    def _compose(self, path, arguments, **kwargs):
        return self.call(['docker', 'compose', '-p', self.manifest['project'], '-f', str(path), *arguments], **kwargs)

    def _inspect_owned(self):
        ids = self.call(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + self.manifest['project']]).split()
        rows = json.loads(self.call(['docker', 'inspect', *ids])) if ids else []
        if not rows or any(r['Config'].get('Labels', {}).get('com.docker.compose.project') != self.manifest['project'] for r in rows):
            raise ValueError('Owned retest containers missing or changed')
        for row in rows:
            if any(not name.startswith(self.manifest['project'] + '_')
                   for name in row.get('NetworkSettings', {}).get('Networks', {})):
                raise ValueError('Actual retest container is attached to a foreign network')
            for mount in row.get('Mounts', []):
                if mount['Type'] == 'volume' and not mount.get('Name', '').startswith(self.manifest['project'] + '_'):
                    raise ValueError('Actual container mounts a foreign volume')
                if mount['Type'] == 'bind' and not Path(mount['Source']).resolve().is_relative_to(self.root / 'source'):
                    raise ValueError('Actual bind mount escapes new source')
        return rows

    def _volumes(self, config, rows):
        result = {}
        for alias, role, target in (('postgres_data', 'db', '/var/lib/postgresql/data'),
                                   ('media_data', 'web', '/app/media')):
            mounts = [mount for row in rows if row['Config']['Labels'].get('com.docker.compose.service') == role
                      for mount in row.get('Mounts', []) if mount['Destination'] == target]
            name = config['volumes'][alias]['name']
            volume = json.loads(self.call(['docker', 'volume', 'inspect', name]))[0]
            result[alias] = validate_volume_record(volume, alias, self.manifest, mounts)
            if not result[alias].is_dir() or result[alias].is_symlink():
                raise ValueError('Actual volume directory absent or symlink')
        for alias, resource in config.get('networks', {}).items():
            record = json.loads(self.call(['docker', 'network', 'inspect', resource['name']]))[0]
            if record['Name'] != resource['name'] or (record.get('Labels') or {}).get('com.docker.compose.project') != self.manifest['project']:
                raise ValueError('Actual network belongs to another project')
        return result

    def preflight_resources(self, config):
        """Before first startup, reject any existing resource with the new name but foreign labels."""
        existing = set(self.call(['docker', 'volume', 'ls', '--format', '{{.Name}}']).split())
        for alias, resource in config.get('volumes', {}).items():
            name = resource['name']
            if name in existing:
                record = json.loads(self.call(['docker', 'volume', 'inspect', name]))[0]
                point = validate_volume_record(record, alias, self.manifest)
                if alias == 'postgres_data' and (point / 'PG_VERSION').read_text().strip() != '17':
                    raise ValueError('Copied PostgreSQL data is not version 17')
            elif alias in ('postgres_data', 'media_data'):
                raise ValueError('Owned cold source data volume is missing before first start')
        networks = set(self.call(['docker', 'network', 'ls', '--format', '{{.Name}}']).split())
        for resource in config.get('networks', {}).values():
            if resource['name'] in networks:
                record = json.loads(self.call(['docker', 'network', 'inspect', resource['name']]))[0]
                if (record['Name'] != resource['name'] or
                        (record.get('Labels') or {}).get('com.docker.compose.project') != self.manifest['project']):
                    raise ValueError('Existing new-name network has foreign ownership')

    def _stop_writers(self, rows):
        writers = [r['Id'] for r in rows if r['Config']['Labels'].get('com.docker.compose.service') != 'db']
        if writers:
            self.call(['docker', 'stop', '--time', '60', *writers], timeout=120)

    def _db(self, path, config, arguments, **kwargs):
        role = config['services']['db']['environment']['POSTGRES_USER']
        return self._compose(path, ['exec', '-T', 'db', arguments[0], '-U', role, *arguments[1:]], **kwargs)

    def _db_ready(self, path, config):
        actual = self._db(path, config, ['psql', '-d', 'postgres', '-At', '-c',
            "SELECT current_setting('server_version_num'),current_setting('max_connections'),"
            "current_setting('superuser_reserved_connections'),current_setting('fsync'),"
            "current_setting('synchronous_commit'),current_setting('full_page_writes')"]).strip().split('|')
        settings = dict(item.split('=', 1) for item in config['services']['db']['command'] if '=' in item)
        expected = [settings['max_connections'], settings['superuser_reserved_connections'], 'on', 'on', 'on']
        if len(actual) != 6 or not 170000 <= int(actual[0]) < 180000 or actual[1:] != expected:
            raise ValueError('Actual PostgreSQL 17 capacity/durability differs from candidate')
        return actual

    def _start(self, path):
        self._compose(path, ['up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '240', *ACTIVE_SERVICES], timeout=300)
        self._compose(path, ['exec', '-T', 'nginx', 'nginx', '-t'])
        rows = self._inspect_owned()
        by_role = {}
        for row in rows:
            by_role.setdefault(row['Config']['Labels'].get('com.docker.compose.service'), []).append(row)
        for role in ACTIVE_SERVICES:
            if role not in by_role or any((not r['State']['Running'] if role != 'migrate' else
                                         r['State'].get('ExitCode') != 0) for r in by_role[role]):
                raise ValueError('Retest service is not ready: ' + role)
        if len(by_role['web']) != 1:
            raise ValueError('Exactly one owned web container required')
        return by_role['web'][0]['Id']

    def create_baseline(self, fixture_path=None):
        config = self._load_config(self.compose_path)
        private = self.root / 'private'
        snapshot = private / 'baseline-snapshot.json'
        dump, media = private / 'baseline.dump', private / 'baseline-media'
        fixture_path = Path(fixture_path or private / 'baseline-fixture.json').resolve()
        if not fixture_path.is_relative_to(private) or fixture_path.is_symlink():
            raise ValueError('Baseline fixture must be an owned private file')
        fixture = json.loads(fixture_path.read_text())
        if (fixture.get('source_ownership', {}).get('command') != 'prepare_student_capacity_baseline'
                or fixture.get('baseline_snapshot_id') != self.manifest['baseline_id']):
            raise ValueError('Published baseline fixture identity differs')
        if any(path.exists() for path in (snapshot, dump, media)):
            raise ValueError('Baseline snapshot already exists; refusing replacement')
        rows = self._inspect_owned()
        volumes = self._volumes(config, rows)
        self._db_ready(self.compose_path, config)
        media_records = tree_manifest(volumes['media_data'])
        database_size = int(self._db(self.compose_path, config, ['psql', '-d', 'postgres', '-At', '-c',
            "SELECT pg_database_size('kt_e2e')"]).strip())
        if shutil.disk_usage(private).free < database_size + sum(r['size'] for r in media_records):
            raise ValueError('Insufficient free disk for database dump and media snapshot')
        self._stop_writers(rows)
        with dump.open('xb') as stream:
            os.chmod(dump, 0o600)
            self._db(self.compose_path, config, ['pg_dump', '-d', 'kt_e2e', '-Fc'], stdout=stream)
            stream.flush()
            os.fsync(stream.fileno())
        _copy_media(volumes['media_data'], media)
        if tree_manifest(volumes['media_data']) != tree_manifest(media):
            raise ValueError('Media snapshot content or ownership differs from the stopped source')
        metadata = {'project': self.manifest['project'], 'database': 'kt_e2e', 'baseline_id': self.manifest['baseline_id'],
            'database_size': database_size,
            'dump_sha256': digest(dump), 'fixture_path': fixture_path.relative_to(private).as_posix(),
            'fixture_sha256': digest(fixture_path), 'fixture_id': fixture['fixture_id'], 'media_manifest': tree_manifest(media)}
        with snapshot.open('x') as stream:
            os.chmod(snapshot, 0o600)
            json.dump(metadata, stream, indent=2)
        self._start(self.compose_path)
        return metadata

    def _verify_snapshot(self):
        private = self.root / 'private'
        metadata = json.loads((private / 'baseline-snapshot.json').read_text())
        fixture = (private / metadata['fixture_path']).resolve()
        if (metadata['project'] != self.manifest['project'] or metadata['database'] != 'kt_e2e'
                or metadata['baseline_id'] != self.manifest['baseline_id'] or not fixture.is_relative_to(private)
                or digest(private / 'baseline.dump') != metadata['dump_sha256']
                or digest(fixture) != metadata['fixture_sha256']
                or tree_manifest(private / 'baseline-media') != metadata['media_manifest']):
            raise ValueError('Baseline snapshot identity or digest differs before restore')
        return metadata

    def restore(self, config_path):
        path = Path(config_path).resolve()
        config = self._load_config(path)
        metadata = self._verify_snapshot()
        rows = self._inspect_owned()
        volumes = self._volumes(config, rows)
        media_root = volumes['media_data']
        tree_manifest(media_root)
        if shutil.disk_usage(media_root).free + sum(r['size'] for r in tree_manifest(media_root)) < sum(
                r['size'] for r in tree_manifest(self.root / 'private/baseline-media')):
            raise ValueError('Insufficient free disk to restore baseline media')
        current_size = int(self._db(path, config, ['psql', '-d', 'postgres', '-At', '-c',
            "SELECT pg_database_size('kt_e2e')"]).strip())
        if shutil.disk_usage(volumes['postgres_data']).free + current_size < metadata['database_size']:
            raise ValueError('Insufficient free disk to restore the frozen database')
        self._stop_writers(rows)
        self._compose(path, ['up', '-d', '--no-deps', '--force-recreate', '--pull', 'never', '--wait', 'db'], timeout=240)
        self._db_ready(path, config)
        current = self._volumes(config, self._inspect_owned())
        if current != volumes:
            raise ValueError('Actual volume mount changed before destructive restore')
        self._db(path, config, ['dropdb', '--if-exists', '--force', 'kt_e2e'])
        self._db(path, config, ['createdb', '-O', config['services']['db']['environment']['POSTGRES_USER'], 'kt_e2e'])
        with (self.root / 'private/baseline.dump').open('rb') as stream:
            self._db(path, config, ['pg_restore', '--exit-on-error', '--no-owner', '-d', 'kt_e2e'], stdin=stream)
        # Every recursive target is checked again after stopping writers and recreating db.
        tree_manifest(media_root)
        for child in media_root.iterdir():
            if not child.resolve().is_relative_to(media_root) or child.is_symlink():
                raise ValueError('Media cleanup target escapes verified owned volume')
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        _copy_media(self.root / 'private/baseline-media', media_root)
        if tree_manifest(media_root) != metadata['media_manifest']:
            raise ValueError('Restored media content or ownership differs from the frozen snapshot')
        self.compose_path = path
        return self._start(path)
