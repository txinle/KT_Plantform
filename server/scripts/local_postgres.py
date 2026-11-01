import argparse
import subprocess
import sys
import time
from pathlib import Path


def ensure_ready(pg_bin, pg_data, host, port, user, *, timeout=600):
    """Keep the shared Windows database independent of the website's console."""
    def run(executable, *args):
        return subprocess.run(
            [str(pg_bin / executable), *args],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).returncode

    def ready():
        return run('pg_isready.exe', '-h', host, '-p', port, '-U', user, '-t', '2')

    def status():
        return run('pg_ctl.exe', '-D', str(pg_data), 'status')

    readiness = ready()
    if readiness == 0:
        print('[runtime] Reusing running PostgreSQL.', flush=True)
        return
    if host not in ('127.0.0.1', 'localhost'):
        raise RuntimeError('The configured remote PostgreSQL server is unavailable.')
    log = pg_data / 'server.log'
    state = status()
    if state == 3 and readiness == 2:
        # pg_ctl and its postgres child must not inherit the launcher console:
        # a broadcast Ctrl+C would otherwise interrupt crash recovery too.
        if run('pg_ctl.exe', '-D', str(pg_data), '-l', str(log), '-o',
               f'-p {int(port)} -h 127.0.0.1', '-W', 'start') != 0:
            raise RuntimeError(f'PostgreSQL could not start. See {log}')
    elif state != 0:
        raise RuntimeError(f'Cannot safely start PostgreSQL (readiness={readiness}, status={state}). See {log}')

    started = time.monotonic()
    next_notice = started
    print(f'[runtime] Waiting for PostgreSQL startup/recovery. Log: {log}', flush=True)
    while True:
        if ready() == 0:
            print('[runtime] PostgreSQL is ready.', flush=True)
            return
        if status() != 0:
            raise RuntimeError(f'PostgreSQL exited during startup/recovery. See {log}')
        now = time.monotonic()
        if now - started >= timeout:
            raise RuntimeError(f'PostgreSQL is still running but not ready after {timeout}s; it was left running. See {log}')
        if now >= next_notice:
            print(f'[runtime] PostgreSQL is starting/recovering ({int(now - started)}s); waiting, without restarting it...', flush=True)
            next_notice = now + 10
        time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    try:
        ensure_ready(args.bin, args.data, args.host, args.port, args.user)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'[ERROR] {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('[runtime] Website startup cancelled; PostgreSQL was left running.', flush=True)
        sys.exit(130)
