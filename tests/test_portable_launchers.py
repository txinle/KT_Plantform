from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class PortableLauncherContractTests(TestCase):
    def test_unix_launcher_matches_local_startup_contract(self):
        launcher = ROOT / "run_local.sh"
        self.assertTrue(launcher.is_file(), "run_local.sh is missing")
        source = launcher.read_text(encoding="utf-8")
        for token in (
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            "sys.version_info >= (3, 10)",
            "import ensurepip",
            ".venv/bin/python",
            ".venv/Scripts/python.exe",
            ".requirements.sha256",
            "manage.py migrate --noinput",
            "manage.py seed_demo",
            "KT_SKIP_BROWSER",
            "webbrowser.open",
            "manage.py runserver 0.0.0.0:8000",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("manage.py makemigrations", source)

    def test_package_builder_includes_unix_artifacts(self):
        source = (ROOT / "scripts" / "create_portable_package.ps1").read_text(
            encoding="utf-8"
        )
        for token in (
            "run_local.sh",
            "create_portable_archive.py",
            "PORTABLE_UNIX_README.md",
            "PACKAGE_TAR_GZ=",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
