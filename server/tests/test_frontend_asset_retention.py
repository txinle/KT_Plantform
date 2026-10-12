import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FrontendAssetRetentionTests(unittest.TestCase):
    def test_release_and_rollback_keep_old_lazy_chunks_available(self):
        script = Path(__file__).resolve().parents[2] / "deploy" / "docker" / "20-frontend-assets.sh"
        shell = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("sh")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, retained = (root / name for name in ("first", "second", "retained"))
            first.mkdir(); second.mkdir()
            (first / "Student-old.js").write_text("old assessment", encoding="utf-8")
            (second / "Student-new.js").write_text("new assessment", encoding="utf-8")
            for release in (first, second, first):
                subprocess.run([shell, script.as_posix(), release.as_posix(), retained.as_posix()], check=True, capture_output=True)
            self.assertEqual((retained / "Student-old.js").read_text(), "old assessment")
            self.assertEqual((retained / "Student-new.js").read_text(), "new assessment")

    def test_release_and_rollback_keep_versioned_shells_for_open_clients(self):
        script = Path(__file__).resolve().parents[2] / 'deploy/docker/20-frontend-assets.sh'
        shell = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('sh')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets, first, second, retained = (root / name for name in ('assets', 'first', 'second', 'retained'))
            for path in (assets, first, second):
                path.mkdir()
            old, new = 'capture-shell-' + 'a' * 20 + '.html', 'capture-shell-' + 'b' * 20 + '.html'
            (first / old).write_text('old build')
            (second / new).write_text('new build')
            for release in (first, second, first):
                subprocess.run([shell, script.as_posix(), assets.as_posix(), retained.as_posix(), release.as_posix()], check=True, capture_output=True)
            self.assertEqual((retained / 'shells' / old).read_text(), 'old build')
            self.assertEqual((retained / 'shells' / new).read_text(), 'new build')
