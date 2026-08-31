import subprocess
import socket
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest import TestCase

from scripts.run_dev import find_available_vite_port, vite_command


ROOT = Path(__file__).resolve().parents[1]


class PortableLauncherContractTests(TestCase):
    def test_dev_runner_uses_the_same_available_strict_vite_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            occupied_port = occupied.getsockname()[1]

            selected_port = find_available_vite_port(
                start=occupied_port,
                stop=occupied_port + 10,
            )

        self.assertNotEqual(selected_port, occupied_port)
        self.assertEqual(
            vite_command(Path("npm.cmd"), selected_port),
            [
                "npm.cmd",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(selected_port),
                "--strictPort",
            ],
        )

    def test_dev_runner_enables_migrated_teacher_vue_modules_by_default(self):
        source = (ROOT / "scripts" / "run_dev.py").read_text(encoding="utf-8")

        self.assertIn(
            'environment["VUE_TEACHER_EXPERIMENTS_ENABLED"] = "1"',
            source,
        )
        self.assertIn(
            'environment["VUE_TEACHER_QUESTION_BANKS_ENABLED"] = "1"',
            source,
        )

    def test_windows_launcher_uses_the_project_portable_postgresql_database(self):
        source = (ROOT / "run_local.bat").read_text(encoding="utf-8")

        for token in (
            "PostgreSQL-17.11-portable\\pgsql\\bin",
            "PostgreSQL-17.11-data",
            'set "POSTGRES_HOST=127.0.0.1"',
            'set "POSTGRES_PORT=55432"',
            'set "POSTGRES_USER=kt_user"',
            'set "POSTGRES_PASSWORD=kt_password"',
            'set "POSTGRES_DB=kt_platform"',
            "pg_isready.exe",
            "pg_ctl.exe",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_windows_launcher_shell_checks_are_safe_for_cmd_delayed_expansion(self):
        source = (ROOT / "run_local.bat").read_text(encoding="utf-8")
        shell_checks = [
            line
            for line in source.splitlines()
            if "manage.py shell -c" in line
        ]

        self.assertGreaterEqual(len(shell_checks), 2)
        for command in shell_checks:
            with self.subTest(command=command):
                self.assertTrue(command.isascii())
                self.assertNotIn("!=", command)

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
