import hashlib
import os
import stat
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.test import TestCase, override_settings

from .prepared_content import _persist_verified_body, persist_display_content


@skipUnless(os.name == 'posix', 'Nginx and the publishing worker use separate POSIX users')
class PreparedStaticContentPermissionsTests(TestCase):
    def test_published_ciphertext_is_readable_by_the_static_server(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(ASSESSMENT_PAPER_ROOT=Path(directory)):
            body = b'{"ciphertext":"already encrypted"}'
            digest = hashlib.sha256(body).hexdigest()
            _persist_verified_body(body, digest)

            path = Path(directory) / f'{digest}.json'
            self.assertEqual(path.read_bytes(), body)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_reused_verified_ciphertext_recovers_static_server_read_access(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(ASSESSMENT_PAPER_ROOT=Path(directory)):
            descriptor = persist_display_content({'question': {'text': 'frozen student projection'}})
            path = Path(directory) / f"{descriptor['sha256']}.json"
            path.chmod(0o600)
            before = path.read_bytes()

            reused = persist_display_content({'question': {'text': 'frozen student projection'}})

            self.assertEqual(reused, descriptor)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
