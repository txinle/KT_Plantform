import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import capacity_retest


def compose_fixture(manifest):
    from student_capacity_retest_store import ACTIVE_SERVICES
    project = manifest['project']
    config = {'name': project, 'volumes': {name: {'name': project + '_' + name} for name in ('postgres_data', 'media_data')},
              'networks': {'default': {'name': project + '_default'}},
              'services': {role: {'environment': {'POSTGRES_DB': 'kt_e2e'}, 'volumes': []}
                           for role in ACTIVE_SERVICES}}
    config['services']['db'].update(command=['postgres', '-c', 'max_connections=64', '-c',
        'superuser_reserved_connections=3', '-c', 'fsync=on', '-c', 'synchronous_commit=on', '-c', 'full_page_writes=on'],
        volumes=[{'type': 'volume', 'source': 'postgres_data', 'target': '/var/lib/postgresql/data'}])
    config['services']['web']['volumes'] = [{'type': 'volume', 'source': 'media_data', 'target': '/app/media'}]
    config['services']['nginx']['ports'] = [{'host_ip': '127.0.0.1', 'published': '18085', 'target': 80, 'protocol': 'tcp'}]
    return config


class RetestStoreSafetyTests(unittest.TestCase):
    def test_restore_stops_every_owned_writer_then_applies_db_config_before_drop(self):
        from student_capacity_retest_store import SnapshotStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'private/baseline-media').mkdir(parents=True)
            (root / 'private/baseline.dump').write_bytes(b'PGDMPverified')
            media, postgres = root / 'owned-media', root / 'owned-postgres'
            media.mkdir()
            postgres.mkdir()
            subject = object.__new__(SnapshotStore)
            subject.root, subject.manifest, subject.compose_path = root, capacity_retest.default_manifest(), root / 'private/candidate.json'
            config = compose_fixture(subject.manifest)
            config['services']['db']['environment']['POSTGRES_USER'] = 'kt_user'
            subject._load_config = Mock(return_value=config)
            subject._verify_snapshot = Mock(return_value={'database_size': 10, 'media_manifest': []})
            subject._inspect_owned = Mock(return_value=[{'Id': role, 'Config': {'Labels': {
                'com.docker.compose.service': role}}} for role in ('db', 'web', 'pool-monitor')])
            subject._volumes = Mock(return_value={'postgres_data': postgres, 'media_data': media})
            subject._db_ready = Mock()
            subject._start = Mock(return_value='new-web-id')
            commands = []
            subject.call = lambda args, **kwargs: commands.append(args) or '0'
            with patch('student_capacity_retest_store._copy_media'):
                self.assertEqual(subject.restore(subject.compose_path), 'new-web-id')
            stop = next(i for i, args in enumerate(commands) if args[:2] == ['docker', 'stop'])
            recreate = next(i for i, args in enumerate(commands) if '--force-recreate' in args)
            drop = next(i for i, args in enumerate(commands) if 'dropdb' in args)
            restore = next(i for i, args in enumerate(commands) if 'pg_restore' in args)
            self.assertLess(stop, recreate)
            self.assertLess(recreate, drop)
            self.assertLess(drop, restore)
            self.assertEqual(commands[stop][-2:], ['web', 'pool-monitor'])
            self.assertNotIn('db', commands[stop])
            self.assertEqual(commands[drop][-1], 'kt_e2e')

    def test_start_explicitly_runs_all_services_and_checks_nginx(self):
        from student_capacity_retest_store import ACTIVE_SERVICES, SnapshotStore
        subject = object.__new__(SnapshotStore)
        subject._compose = Mock()
        subject._inspect_owned = Mock(return_value=[{'Id': role, 'Config': {'Labels': {
            'com.docker.compose.service': role}}, 'State': {'Running': role != 'migrate', 'ExitCode': 0}}
            for role in ACTIVE_SERVICES])
        self.assertEqual(subject._start(Path('candidate.json')), 'web')
        first = subject._compose.call_args_list[0].args[1]
        self.assertEqual(set(first[-len(ACTIVE_SERVICES):]), set(ACTIVE_SERVICES))
        self.assertEqual(subject._compose.call_args_list[1].args[1], ['exec', '-T', 'nginx', 'nginx', '-t'])

    def test_compose_rejects_other_project_port_database_volume_and_bind_before_mutation(self):
        from student_capacity_retest_store import validate_owned_compose
        manifest = capacity_retest.default_manifest()
        path = manifest['root'] + '/private/compose-R0.json'
        valid = compose_fixture(manifest)
        validate_owned_compose(valid, path, manifest)
        changes = [lambda c: c.update(name='old-project'),
                   lambda c: c['services']['nginx']['ports'][0].update(published='18084'),
                   lambda c: c['services']['db']['environment'].update(POSTGRES_DB='kt_platform'),
                   lambda c: c['volumes']['media_data'].update(name='old-project_media_data'),
                   lambda c: c['services']['web']['volumes'].append(
                       {'type': 'bind', 'source': '/home/ktdata/student-capacity-tests/20261016/source', 'target': '/app/old'})]
        for change in changes:
            bad = copy.deepcopy(valid)
            change(bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_owned_compose(bad, path, manifest)

    def test_volume_ownership_and_real_mount_identity_must_match(self):
        from student_capacity_retest_store import validate_volume_record
        manifest = capacity_retest.default_manifest()
        name = manifest['project'] + '_media_data'
        volume = {'Name': name, 'Mountpoint': '/var/lib/docker/volumes/' + name + '/_data',
                  'Labels': {'com.docker.compose.project': manifest['project'], 'com.docker.compose.volume': 'media_data'}}
        mount = {'Type': 'volume', 'Name': name, 'Source': volume['Mountpoint'], 'Destination': '/app/media'}
        validate_volume_record(volume, 'media_data', manifest, [mount])
        for field, value in [('Name', 'old_media_data'), ('Mountpoint', '/var/lib/docker/volumes/old/_data')]:
            bad = copy.deepcopy(volume)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_volume_record(bad, 'media_data', manifest, [mount])
        volume['Labels']['com.docker.compose.project'] = 'old-project'
        with self.assertRaises(ValueError):
            validate_volume_record(volume, 'media_data', manifest, [mount])

    def test_volume_resolving_to_original_directory_is_rejected(self):
        from student_capacity_retest_store import validate_volume_record
        manifest = capacity_retest.default_manifest()
        name = manifest['project'] + '_media_data'
        point = '/var/lib/docker/volumes/' + name + '/_data'
        volume = {'Name': name, 'Mountpoint': point, 'Labels': {
            'com.docker.compose.project': manifest['project'], 'com.docker.compose.volume': 'media_data'}}
        mounts = [{'Type': 'volume', 'Name': name, 'Source': point}]
        with patch('student_capacity_retest_store.Path.resolve', return_value=Path('/var/lib/docker/volumes/old_media_data/_data')):
            with self.assertRaisesRegex(ValueError, 'resolved|mount'):
                validate_volume_record(volume, 'media_data', manifest, mounts)

    def test_modified_dump_is_rejected_before_stop_or_any_database_command(self):
        from student_capacity_retest_store import SnapshotStore, tree_manifest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / 'private'
            private.mkdir()
            (private / 'baseline-media').mkdir()
            dump = private / 'baseline.dump'
            dump.write_bytes(b'tampered')
            fixture = private / 'baseline-fixture.json'
            fixture.write_text('{}')
            subject = object.__new__(SnapshotStore)
            subject.root, subject.manifest, subject.compose_path = root, capacity_retest.default_manifest(), private / 'compose-R0.json'
            subject.call = Mock()
            subject._load_config = Mock(return_value={})
            (private / 'baseline-snapshot.json').write_text(json.dumps({
                'project': subject.manifest['project'], 'database': 'kt_e2e',
                'baseline_id': subject.manifest['baseline_id'], 'dump_sha256': hashlib.sha256(b'original').hexdigest(),
                'fixture_path': 'baseline-fixture.json', 'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
                'media_manifest': tree_manifest(private / 'baseline-media')}))
            with self.assertRaisesRegex(ValueError, 'digest'):
                subject.restore(subject.compose_path)
            subject.call.assert_not_called()

    def test_tree_manifest_detects_content_changes_and_rejects_symlinks(self):
        from student_capacity_retest_store import tree_manifest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').write_bytes(b'first')
            first = tree_manifest(root)
            (root / 'data').write_bytes(b'other')
            self.assertNotEqual(first, tree_manifest(root))
            try:
                (root / 'link').symlink_to(root / 'data')
            except OSError:
                self.skipTest('Host does not permit test symlinks')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                tree_manifest(root)


if __name__ == '__main__':
    unittest.main()
