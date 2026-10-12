import hashlib
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT.parent / "client"


def find_node_directory():
    configured = os.getenv("KT_NODE_DIR")
    candidates = [Path(configured)] if configured else []
    if os.name == "nt":
        candidates.extend(
            sorted(
                Path(os.getenv("KT_TOOLING_ROOT", "E:/ATOP/ATOP-tooling")).glob(
                    "node-v24*-win-x64"
                ),
                reverse=True,
            )
        )
    discovered = shutil.which("node")
    if discovered:
        candidates.append(Path(discovered).parent)
    for directory in candidates:
        executable = directory / ("node.exe" if os.name == "nt" else "node")
        npm = directory / ("npm.cmd" if os.name == "nt" else "npm")
        if not executable.exists() or not npm.exists():
            continue
        version = subprocess.check_output([str(executable), "--version"], text=True).strip()
        if version.startswith("v24."):
            return directory, npm
    raise SystemExit("Node 24 LTS was not found. Install it or set KT_NODE_DIR.")


def ensure_frontend_dependencies(npm, *, environment):
    lockfile = FRONTEND / "package-lock.json"
    stamp = FRONTEND / "node_modules" / ".package-lock.sha256"
    digest = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == digest:
        return
    subprocess.run([str(npm), "ci"], cwd=FRONTEND, env=environment, check=True)
    stamp.write_text(digest, encoding="utf-8")


def process_creation_kwargs():
    if os.name == "nt":
        return {
            "creationflags": (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        }
    return {"start_new_session": True}


def terminate_process_tree(process, *, timeout=5):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=timeout)


def ensure_ports_available():
    for port in (8000, 8001):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as error:
                raise SystemExit(f"Port {port} is already in use. Stop the existing local server first.") from error


def wait_for_startup(children):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError("A local service exited during startup; see its output above.")
        try:
            for path in ("/health/", "/"):
                with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=1) as response:
                    if response.status != 200:
                        raise OSError("Service is not ready")
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError("Local services did not become ready within 30 seconds.")


def main():
    ensure_ports_available()
    environment = os.environ.copy()
    if not environment.get("POSTGRES_HOST"):
        raise SystemExit("POSTGRES_HOST is required. Use run_local.bat or run_local.sh; SQLite is not a local launcher fallback.")
    node_directory, npm = find_node_directory()
    environment["PATH"] = f"{node_directory}{os.pathsep}{environment.get('PATH', '')}"
    environment["KT_GATEWAY_PORT"] = "8000"
    environment["KT_GATEWAY_HOST"] = "0.0.0.0"
    environment["KT_BACKEND_ORIGIN"] = "http://127.0.0.1:8001"
    ensure_frontend_dependencies(npm, environment=environment)
    subprocess.run([str(npm), "run", "build"], cwd=FRONTEND, env=environment, check=True)
    print(f"Checkout: {ROOT}", flush=True)
    print("Frontend gateway: http://127.0.0.1:8000 (client build watch)", flush=True)
    commands = (
        ([str(npm), "run", "dev:watch"], FRONTEND),
        ([sys.executable, "manage.py", "runserver", "127.0.0.1:8001"], ROOT),
        ([str(node_directory / ("node.exe" if os.name == "nt" else "node")), "scripts/frontend-gateway.mjs"], FRONTEND),
    ) + tuple(
        ([sys.executable, "manage.py", command], ROOT)
        for command in ("run_scheduler", "run_research_materializations", "run_publication_jobs", "run_student_comparisons")
    )
    children = []
    try:
        for command, directory in commands:
            children.append(subprocess.Popen(command, cwd=directory, env=environment, **process_creation_kwargs()))
        wait_for_startup(children)
        print("[READY] http://127.0.0.1:8000/ - Ctrl+C stops this checkout's services.", flush=True)
        if environment.get("KT_SKIP_BROWSER") != "1":
            webbrowser.open("http://127.0.0.1:8000/")
        while all(child.poll() is None for child in children):
            time.sleep(0.25)
        return next(child.returncode for child in children if child.returncode is not None)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            terminate_process_tree(child)


if __name__ == "__main__":
    raise SystemExit(main())
