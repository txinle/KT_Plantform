"""Executable boundaries for the approved server/client repository layout."""
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]


class RepositoryLayoutTests(TestCase):
    def test_business_sources_have_exactly_two_roots(self):
        for relative in (
            'server/manage.py', 'server/apps', 'server/config',
            'server/requirements.txt', 'client/package.json', 'client/src',
            'contracts/openapi.yaml', 'deploy/docker-compose.yml',
        ):
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).exists(), relative)
        for obsolete in ('apps', 'config', 'frontend', 'manage.py', 'requirements.txt'):
            with self.subTest(obsolete=obsolete):
                self.assertFalse((ROOT / obsolete).exists(), obsolete)

    def test_server_image_build_does_not_require_node_or_client_source(self):
        dockerfile = ROOT / 'server' / 'Dockerfile'
        self.assertTrue(dockerfile.exists(), 'server/Dockerfile')
        source = dockerfile.read_text(encoding='utf-8')
        self.assertNotIn('FROM node:', source)
        self.assertNotIn('COPY client/', source)
        self.assertNotIn('COPY frontend/', source)

    def test_client_contract_check_does_not_start_python_or_django(self):
        script = ROOT / 'client' / 'scripts' / 'check-api-types.mjs'
        self.assertTrue(script.exists(), 'client/scripts/check-api-types.mjs')
        source = script.read_text(encoding='utf-8')
        self.assertNotIn('manage.py', source)
        self.assertNotIn('localPython', source)
        self.assertIn('contracts', source)

    def test_client_image_can_build_without_server_source(self):
        dockerfile = ROOT / 'client' / 'Dockerfile'
        self.assertTrue(dockerfile.exists(), 'client/Dockerfile')
        source = dockerfile.read_text(encoding='utf-8')
        self.assertNotIn('COPY server/', source)
        self.assertNotIn('--from=python', source)
