import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FrontendAssetRetentionTests(unittest.TestCase):
    def test_release_and_rollback_keep_old_lazy_chunks_available(self):
        script = Path(__file__).resolve().parents[1] / "docker" / "20-frontend-assets.sh"
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
