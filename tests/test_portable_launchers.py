import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class PortableLauncherContractTests(TestCase):
    def test_dev_runner_enables_teacher_experiment_vue_by_default(self):
        source = (ROOT / "scripts" / "run_dev.py").read_text(encoding="utf-8")

        self.assertIn(
            'environment["VUE_TEACHER_EXPERIMENTS_ENABLED"] = "1"',
            source,
        )

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
            "scripts/run_dev.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("manage.py makemigrations", source)
        dev_runner = (ROOT / "scripts" / "run_dev.py").read_text(encoding="utf-8")
        for token in ('"manage.py"', '"runserver"', '"0.0.0.0:8000"'):
            with self.subTest(dev_runner_token=token):
                self.assertIn(token, dev_runner)

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


class PortableArchiveTests(TestCase):
    def test_tar_archive_marks_unix_launcher_executable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            source.mkdir()
            (source / "run_local.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8"
            )
            (source / "manage.py").write_text("", encoding="utf-8")
            archive = root / "package.tar.gz"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "create_portable_archive.py"),
                    "--source",
                    str(source),
                    "--output",
                    str(archive),
                ],
                check=True,
            )
            with tarfile.open(archive, "r:gz") as tar:
                members = {member.name: member for member in tar.getmembers()}
            self.assertEqual(members["run_local.sh"].mode, 0o755)
            self.assertEqual(members["manage.py"].mode, 0o644)
