from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import build_student_capacity_remote as builder


class RetestImageBuildTests(unittest.TestCase):
    def test_r0_rejects_old_profile_or_worker_count(self):
        import capacity_retest
        manifest = capacity_retest.default_manifest()
        config = {'services': {role: {'environment': {'GUNICORN_WORKERS': manifest['r0'][key],
            'SCHOOL_POOL_PROFILE': 'shared-entry-v3', 'SCHOOL_REDIS_SESSION_ENABLED': '0'}}
            for role, key in capacity_retest.HTTP_WORKERS.items()}}
        builder.validate_r0_config(config, manifest)
        config['services']['entry']['environment']['GUNICORN_WORKERS'] = '2'
        with self.assertRaisesRegex(ValueError, 'R0'):
            builder.validate_r0_config(config, manifest)
    def test_existing_unproven_image_rejects_before_start_or_status_mutation(self):
        import capacity_retest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'evidence').mkdir()
            (root / 'private').mkdir()
            manifest = capacity_retest.default_manifest() | {'root': str(root), 'backend_image': 'new-backend:retest',
                                                           'frontend_image': 'new-frontend:retest'}
            (root / 'evidence/compose-preflight.json').write_text(json.dumps({
                'project': manifest['project'], 'source_archive_sha256': manifest['source_archive_sha256']}))
            with patch.object(builder, 'validate_manifest', side_effect=lambda value: value), \
                    patch.object(builder, '_context_digest', return_value='source'), \
                    patch.object(builder, 'call', return_value='sha256:base'), \
                    patch.object(builder.subprocess, 'run', return_value=Mock(returncode=0, stdout='sha256:existing')), \
                    patch('student_capacity_retest_store.SnapshotStore') as store, \
                    self.assertRaisesRegex(ValueError, 'Existing image'):
                builder.build_retest(manifest)
            store.assert_not_called()
            self.assertFalse((root / 'private/retest-build.json').exists())

    def test_existing_tag_requires_same_source_and_recorded_image_id(self):
        self.assertTrue(builder.image_resume_allowed('sha256:installed', {'context_sha256': 'source',
            'backend_image_id': 'sha256:installed'}, 'backend', 'source'))
        for state in ({}, {'context_sha256': 'other', 'backend_image_id': 'sha256:installed'},
                      {'context_sha256': 'source', 'backend_image_id': 'sha256:other'}):
            self.assertFalse(builder.image_resume_allowed('sha256:installed', state, 'backend', 'source'))

    def test_retest_dockerfiles_use_verified_local_tags_not_digest_as_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builder.write_retest_dockerfiles(root, {'backend': 'sha256:backend', 'frontend': 'sha256:frontend'})
            backend = (root / 'Dockerfile.capacity-backend').read_text()
            frontend = (root / 'Dockerfile.capacity-frontend').read_text()
            self.assertTrue(backend.startswith('FROM kt-capacity-base-backend:backend\n'))
            self.assertIn('COPY server/ /app/', backend)
            self.assertTrue(frontend.startswith('FROM kt-capacity-base-frontend:frontend\n'))
            for source in ('client/dist/spa-assets/', 'client/legacy/js/', 'deploy/docker/20-frontend-assets.sh'):
                self.assertIn(source, frontend)
            self.assertIn('server/.env', (root / '.dockerignore').read_text())

    def test_local_base_tag_must_resolve_to_the_recorded_image_id(self):
        with patch.object(builder, 'call', side_effect=['', 'sha256:other']), \
                self.assertRaisesRegex(ValueError, 'base image'):
            builder.pin_local_bases({'backend': 'sha256:expected'})


if __name__ == '__main__':
    unittest.main()
