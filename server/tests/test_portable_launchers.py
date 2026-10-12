import hashlib
import os
import subprocess
import socket
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest import skipUnless
from unittest.mock import Mock, patch

from scripts import run_dev


ROOT = Path(__file__).resolve().parents[1]


class PortableLauncherContractTests(TestCase):
    @skipUnless(sys.platform == 'win32', 'Windows batch dependency cache')
    def test_windows_dependency_stamp_is_valid_and_skips_second_install(self):
        source = (ROOT.parent / 'run_local.bat').read_text(encoding='utf-8')
        block = source.split('echo [2/4]', 1)[1].split('echo [3/4]', 1)[0]
        block = block.split('\n', 1)[1]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / 'requirements.txt').write_text('', encoding='utf-8')
            launcher = root / 'dependency-check.cmd'
            launcher.write_text(
                '@echo off\nsetlocal EnableExtensions EnableDelayedExpansion\n'
                f'set "VENV_PYTHON={sys.executable}"\n'
                'set "REQUIREMENTS_STAMP=stamp"\n' + block + '\nexit /b 0\n:fail\nexit /b 1\n',
                encoding='utf-8',
            )
            environment = {**os.environ, 'PIP_DISABLE_PIP_VERSION_CHECK': '1'}
            first = subprocess.run(['cmd.exe', '/d', '/c', str(launcher)], cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual((root / 'stamp').read_text().strip().lower(), hashlib.sha256(b'').hexdigest())
            second = subprocess.run(['cmd.exe', '/d', '/c', str(launcher)], cwd=root, env=environment, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn('Dependencies are unchanged', second.stdout)

    def test_local_launchers_use_postgresql_and_never_reset_existing_demo_data(self):
        windows = (ROOT.parent / 'run_local.bat').read_text(encoding='utf-8')
        unix = (ROOT.parent / 'run_local.sh').read_text(encoding='utf-8')
        self.assertNotIn('--reset-question-bank', windows)
        self.assertNotIn('db.sqlite3', unix)
        for name in ('POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB'):
            self.assertIn(name, unix)
        self.assertIn('pg_isready', unix)
        self.assertIn('cmd.exe', unix)
        self.assertIn('MSYS_NO_PATHCONV=1', unix)
        self.assertIn('run_local.bat', unix)

    def test_windows_launcher_uses_moved_tools_and_project_caches(self):
        source = (ROOT.parent / 'run_local.bat').read_text(encoding='utf-8')
        self.assertIn(r'E:\ATOP\ATOP-tooling', source)
        self.assertNotIn(r'%USERPROFILE%\Downloads\ATOP-tooling', source)
        for name in ('KT_NODE_DIR', 'PIP_CACHE_DIR', 'npm_config_cache', 'TEMP', 'TMP', 'PLAYWRIGHT_BROWSERS_PATH'):
            self.assertIn(name, source)
        self.assertIn('8001', source)
        self.assertIn('sys.version_info[:2] == (3, 13)', source)

    def test_busy_backend_port_rejects_startup_before_installing_or_building(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        with (
            socket.socket() as listener,
            patch.object(run_dev, 'find_node_directory', return_value=(Path('node'), Path('npm.cmd'))),
            patch.object(run_dev, 'ensure_frontend_dependencies') as install,
            patch.object(run_dev.subprocess, 'run'),
            patch.object(run_dev.subprocess, 'Popen', return_value=process),
        ):
            listener.bind(('127.0.0.1', 8001))
            listener.listen()
            with self.assertRaisesRegex(SystemExit, '8001'):
                run_dev.main()
            install.assert_not_called()

    def test_runner_builds_client_then_starts_private_backend_and_node_gateway(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        with (
            patch.dict(run_dev.os.environ, {'POSTGRES_HOST': '127.0.0.1'}, clear=True),
            patch.object(run_dev, 'find_node_directory', return_value=(Path('node'), Path('npm.cmd'))),
            patch.object(run_dev, 'ensure_frontend_dependencies') as install,
            patch.object(run_dev, 'wait_for_startup', create=True),
            patch('webbrowser.open'),
            patch.object(run_dev.subprocess, 'run') as build,
            patch.object(run_dev.subprocess, 'Popen', return_value=process) as start,
        ):
            self.assertEqual(run_dev.main(), 0)
        self.assertEqual(build.call_args.args[0], ['npm.cmd', 'run', 'build'])
        self.assertEqual(start.call_args_list[0].args[0], ['npm.cmd', 'run', 'dev:watch'])
        self.assertEqual(start.call_args_list[1].args[0], [sys.executable, 'manage.py', 'runserver', '127.0.0.1:8001'])
        self.assertEqual(start.call_args_list[2].args[0], [str(Path('node') / ('node.exe' if sys.platform == 'win32' else 'node')), 'scripts/frontend-gateway.mjs'])
        self.assertEqual(start.call_args_list[2].kwargs['cwd'], run_dev.FRONTEND)
        self.assertEqual(start.call_args_list[2].kwargs['env']['KT_BACKEND_ORIGIN'], 'http://127.0.0.1:8001')
        self.assertEqual(start.call_args_list[2].kwargs['env']['KT_GATEWAY_PORT'], '8000')
        self.assertEqual(install.call_args.kwargs.get('environment'), start.call_args_list[2].kwargs['env'])
        commands = [call.args[0] for call in start.call_args_list]
        for command in ('run_scheduler', 'run_research_materializations', 'run_publication_jobs', 'run_student_comparisons'):
            self.assertIn([sys.executable, 'manage.py', command], commands)

    def test_browser_opens_only_after_backend_and_gateway_are_ready(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        phases = []
        with (
            patch.dict(run_dev.os.environ, {'POSTGRES_HOST': '127.0.0.1'}, clear=True),
            patch.object(run_dev, 'find_node_directory', return_value=(Path('node'), Path('npm.cmd'))),
            patch.object(run_dev, 'ensure_frontend_dependencies'),
            patch.object(run_dev.subprocess, 'run'),
            patch.object(run_dev.subprocess, 'Popen', return_value=process),
            patch.object(run_dev, 'wait_for_startup', create=True, side_effect=lambda *_: phases.append('ready')),
            patch('webbrowser.open', side_effect=lambda *_: phases.append('browser')),
        ):
            self.assertEqual(run_dev.main(), 0)
        self.assertEqual(phases, ['ready', 'browser'])

    @skipUnless(sys.platform == "win32", "Windows process-tree contract")
    def test_windows_shutdown_stops_the_entire_child_process_tree(self):
        self.assertTrue(hasattr(run_dev, "process_creation_kwargs"))
        self.assertTrue(hasattr(run_dev, "terminate_process_tree"))

        parent = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess, sys, time; "
                    "child = subprocess.Popen([sys.executable, '-c', "
                    "'import time; time.sleep(60)']); "
                    "print(child.pid, flush=True); time.sleep(60)"
                ),
            ],
            stdout=subprocess.PIPE,
            text=True,
            **run_dev.process_creation_kwargs(),
        )
        self.addCleanup(
            subprocess.run,
            ["taskkill", "/PID", str(parent.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        child_pid = int(parent.stdout.readline().strip())
        parent.stdout.close()

        run_dev.terminate_process_tree(parent, timeout=1)
        parent.wait(timeout=5)

        for _ in range(20):
            child_status = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            if str(child_pid) not in child_status:
                break
            time.sleep(0.1)
        self.assertNotIn(str(child_pid), child_status)

    def test_runner_has_no_legacy_or_python_served_frontend_mode(self):
        source = (ROOT / "scripts" / "run_dev.py").read_text(encoding="utf-8")

        self.assertNotIn('KT_LEGACY_ONLY', source)
        self.assertNotIn('VITE_DEV_SERVER_URL', source)
        self.assertNotIn('VUE_STANDALONE_ENABLED', source)

    def test_windows_launcher_uses_the_project_portable_postgresql_database(self):
        source = (ROOT.parent / "run_local.bat").read_text(encoding="utf-8")

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
        source = (ROOT.parent / "run_local.bat").read_text(encoding="utf-8")
        shell_checks = [
            line
            for line in source.splitlines()
            if "manage.py shell -c" in line
        ]

        self.assertGreaterEqual(len(shell_checks), 1)
        for command in shell_checks:
            with self.subTest(command=command):
                self.assertTrue(command.isascii())
                self.assertNotIn("!=", command)

    def test_unix_launcher_matches_local_startup_contract(self):
        launcher = ROOT.parent / "run_local.sh"
        self.assertTrue(launcher.is_file(), "run_local.sh is missing")
        source = launcher.read_text(encoding="utf-8")
        for token in (
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            "sys.version_info[:2] == (3, 13)",
            "import ensurepip",
            ".venv/bin/python",
            ".venv/Scripts/python.exe",
            ".requirements.sha256",
            "manage.py migrate --noinput",
            "call_command('seed_demo')",
            "scripts/run_dev.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("manage.py makemigrations", source)
        dev_runner = (ROOT / "scripts" / "run_dev.py").read_text(encoding="utf-8")
        for token in ('"manage.py"', '"runserver"', '"127.0.0.1:8001"', '"scripts/frontend-gateway.mjs"'):
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
