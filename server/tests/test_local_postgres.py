import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

from scripts import local_postgres


@skipUnless(sys.platform == 'win32', 'Windows PostgreSQL launcher')
class LocalPostgresTests(TestCase):
    def invoke(self, codes, *, timeout=600):
        def execute(command, **kwargs):
            return subprocess.CompletedProcess(command, next(results))

        results = iter(codes)
        with (
            patch.object(local_postgres.subprocess, 'run', side_effect=execute) as run,
            patch.object(local_postgres.time, 'sleep'),
        ):
            local_postgres.ensure_ready(Path('bin'), Path('data'), '127.0.0.1', '55432', 'kt_user', timeout=timeout)
        return run.call_args_list

    def test_ready_database_is_reused(self):
        calls = self.invoke([0])
        self.assertEqual(len(calls), 1)

    def test_recovering_database_is_waited_for_without_second_start(self):
        calls = self.invoke([1, 0, 1, 0, 0])
        self.assertEqual(len(calls), 5)
        self.assertFalse(any('start' in call.args[0] for call in calls))

    def test_stopped_database_starts_once_without_console(self):
        calls = self.invoke([2, 3, 0, 0])
        starts = [call for call in calls if 'start' in call.args[0]]
        self.assertEqual(len(starts), 1)
        self.assertIn('-W', starts[0].args[0])
        self.assertEqual(starts[0].kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_timeout_leaves_recovering_database_running(self):
        with self.assertRaisesRegex(RuntimeError, 'still running.*server.log'):
            self.invoke([1, 0, 1, 0], timeout=0)

    def test_exit_during_startup_reports_log(self):
        with self.assertRaisesRegex(RuntimeError, 'exited.*server.log'):
            self.invoke([2, 3, 0, 2, 3])

    def test_failed_start_reports_log_without_retrying(self):
        with self.assertRaisesRegex(RuntimeError, 'could not start.*server.log'):
            self.invoke([2, 3, 1])

    def test_unowned_rejecting_server_is_not_started_over(self):
        with self.assertRaisesRegex(RuntimeError, 'Cannot safely start'):
            self.invoke([1, 3])

    def test_remote_failure_never_starts_local_database(self):
        with patch.object(local_postgres.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2)) as run:
            with self.assertRaisesRegex(RuntimeError, 'remote'):
                local_postgres.ensure_ready(Path('bin'), Path('data'), 'db.example', '5432', 'kt_user')
        self.assertEqual(run.call_count, 1)

    def test_windows_provisioner_uses_console_isolated_startup(self):
        script = (Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_local_runtime.ps1').read_text(encoding='utf-8')
        self.assertIn("'local_postgres.py'", script)
        self.assertNotIn('-w start', script)


@skipUnless(sys.platform == 'win32' and os.getenv('KT_POSTGRES_BIN'), 'Requires Windows PostgreSQL 17 binaries')
class PostgresConsoleTests(TestCase):
    def test_console_ctrl_c_preserves_database_and_next_start_reuses_it(self):
        pg_bin = Path(os.environ['KT_POSTGRES_BIN'])
        with tempfile.TemporaryDirectory(prefix='kt pg console ') as temporary:
            root = Path(temporary)
            # initdb embeds its installation path in bootstrap SQL on Windows.
            # A junction avoids the shared runtime's non-ASCII installation path.
            alias = root / 'pgsql'
            subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(alias), str(pg_bin.parent)],
                           check=True, capture_output=True)
            pg_bin = alias / 'bin'
            data = root / 'data'
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = str(probe.getsockname()[1])
            initialized = subprocess.run([str(pg_bin / 'initdb.exe'), '-D', str(data), '-U', 'kt_user',
                            '--auth=trust', '--encoding=UTF8', '--locale=C'],
                           capture_output=True, timeout=60)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            ready = root / 'ready'
            interrupted = root / 'interrupted'
            host_script = root / 'host.py'
            host_script.write_text(
                'import sys, time\nfrom pathlib import Path\n'
                f'sys.path.insert(0, {str(Path(local_postgres.__file__).parent)!r})\n'
                'from local_postgres import ensure_ready\n'
                f'ensure_ready(Path({str(pg_bin)!r}), Path({str(data)!r}), "127.0.0.1", {port!r}, "kt_user")\n'
                'try:\n'
                f'    Path({str(ready)!r}).touch()\n'
                '    while True: time.sleep(0.1)\n'
                'except KeyboardInterrupt:\n'
                f'    Path({str(interrupted)!r}).touch()\n', encoding='utf-8')
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            host = subprocess.Popen([sys.executable, str(host_script)],
                                    creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
            try:
                deadline = time.monotonic() + 45
                while not ready.exists() and host.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertTrue(ready.exists(), 'Console host did not start the isolated database')
                original_pid = (data / 'postmaster.pid').read_bytes().splitlines()[0]
                # Broadcast an actual Ctrl+C to the host's console, never ours.
                sender = (
                    'import ctypes, time\n'
                    'k = ctypes.WinDLL("kernel32", use_last_error=True)\n'
                    'k.FreeConsole()\n'
                    f'assert k.AttachConsole({host.pid})\n'
                    'assert k.SetConsoleCtrlHandler(None, True)\n'
                    'assert k.GenerateConsoleCtrlEvent(0, 0)\n'
                    'time.sleep(0.5)\n'
                )
                subprocess.run([sys.executable, '-c', sender], check=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                host.wait(timeout=10)
                self.assertTrue(interrupted.exists(), 'Host did not receive Ctrl+C')
                readiness = subprocess.run([str(pg_bin / 'pg_isready.exe'), '-h', '127.0.0.1',
                                            '-p', port, '-U', 'kt_user'], capture_output=True)
                self.assertEqual(readiness.returncode, 0, readiness.stdout)
                local_postgres.ensure_ready(pg_bin, data, '127.0.0.1', port, 'kt_user')
                self.assertEqual((data / 'postmaster.pid').read_bytes().splitlines()[0], original_pid)
            finally:
                if host.poll() is None:
                    host.terminate()
                    host.wait(timeout=10)
                subprocess.run([str(pg_bin / 'pg_ctl.exe'), '-D', str(data), '-m', 'fast', '-w', 'stop'],
                               capture_output=True, timeout=30)
