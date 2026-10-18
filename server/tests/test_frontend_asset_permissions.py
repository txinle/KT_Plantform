import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix', 'Requires Unix filesystem permissions and /bin/sh')
class FrontendAssetPermissionTests(unittest.TestCase):
    def test_private_archive_modes_become_readable_only_for_public_assets(self):
        script = Path(__file__).resolve().parents[2] / 'deploy/docker/20-frontend-assets.sh'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            (source / 'chunk.js').write_text('export const value = 1;')
            shells = root / 'shells'
            shells.mkdir()
            (shells / 'capture-shell-example.html').write_text('<html>example</html>')
            public = root / 'public'
            icons = public / 'icons'
            icons.mkdir(parents=True)
            icon = icons / 'beian-police.png'
            icon.write_bytes(b'original-icon')
            retained = public / 'assets/retained.js'
            retained.parent.mkdir()
            retained.write_text('original chunk')
            private = root / 'private'
            private.mkdir(mode=0o700)
            secret = private / 'credentials'
            secret.write_text('private')
            secret.chmod(0o600)
            for path in public.rglob('*'):
                path.chmod(0o700 if path.is_dir() else 0o600)
            public.chmod(0o700)
            previous_umask = os.umask(0o077)
            try:
                subprocess.run(['/bin/sh', str(script), str(source), str(public / 'assets'), str(shells)], check=True)
            finally:
                os.umask(previous_umask)
            for path in [public, *public.rglob('*')]:
                expected = 0o755 if path.is_dir() else 0o644
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, str(path.relative_to(root)))
            self.assertEqual(icon.read_bytes(), b'original-icon')
            self.assertEqual(retained.read_text(), 'original chunk')
            self.assertTrue((public / 'assets/chunk.js').is_file())
            self.assertTrue((public / 'assets/shells/capture-shell-example.html').is_file())
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
