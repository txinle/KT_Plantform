import hashlib
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def find_node_directory():
    configured = os.getenv("KT_NODE_DIR")
    candidates = [Path(configured)] if configured else []
    if os.name == "nt":
        candidates.extend(
            sorted(
                (Path.home() / "Downloads" / "ATOP-tooling").glob(
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


def ensure_frontend_dependencies(npm):
    lockfile = FRONTEND / "package-lock.json"
    stamp = FRONTEND / "node_modules" / ".package-lock.sha256"
    digest = hashlib.sha256(lockfile.read_bytes()).hexdigest()
    if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == digest:
        return
    subprocess.run([str(npm), "ci"], cwd=FRONTEND, check=True)
    stamp.write_text(digest, encoding="utf-8")


def find_available_vite_port(*, start=5173, stop=5273):
    for port in range(start, stop + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
                candidate.bind(("127.0.0.1", port))
        except OSError:
            continue
        return port
    raise SystemExit(f"No available Vite port was found between {start} and {stop}.")


def vite_command(npm, port):
    return [
        str(npm),
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--strictPort",
    ]


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


def main():
    legacy_only = os.getenv("KT_LEGACY_ONLY") == "1"
    environment = os.environ.copy()
    if legacy_only:
        environment["VUE_STANDALONE_ENABLED"] = "0"
        environment["VUE_TEACHER_COURSES_ENABLED"] = "0"
        environment["VUE_TEACHER_EXPERIMENTS_ENABLED"] = "0"
        environment["VUE_TEACHER_QUESTION_BANKS_ENABLED"] = "0"
        environment["VUE_TEACHER_CLASSES_ENABLED"] = "0"
        environment["VUE_TEACHER_DATA_CENTER_ENABLED"] = "0"
        environment["VUE_STUDENT_COURSES_ENABLED"] = "0"
        environment["VUE_STUDENT_ASSESSMENT_ENABLED"] = "0"
        return subprocess.call(
            [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"],
            cwd=ROOT,
            env=environment,
        )

    environment["VUE_TEACHER_COURSES_ENABLED"] = "1"
    environment["VUE_TEACHER_EXPERIMENTS_ENABLED"] = "1"
    environment["VUE_TEACHER_QUESTION_BANKS_ENABLED"] = "1"
    environment["VUE_TEACHER_CLASSES_ENABLED"] = "1"
    environment["VUE_TEACHER_DATA_CENTER_ENABLED"] = "1"
    environment["VUE_STUDENT_COURSES_ENABLED"] = "1"
    environment["VUE_STUDENT_ASSESSMENT_ENABLED"] = "1"
    node_directory, npm = find_node_directory()
    environment["PATH"] = f"{node_directory}{os.pathsep}{environment.get('PATH', '')}"
    ensure_frontend_dependencies(npm)
    standalone = environment.get("VUE_STANDALONE_ENABLED", "1") == "1"
    if standalone:
        environment["VUE_STANDALONE_ENABLED"] = "1"
        environment["VITE_DEV_SERVER_URL"] = ""
        subprocess.run([str(npm), "run", "build"], cwd=FRONTEND, env=environment, check=True)
        frontend_command = [str(npm), "run", "build", "--", "--watch"]
        frontend_display = "independent Vite build (watch); same-origin http://127.0.0.1:8000"
    else:
        vite_port = find_available_vite_port()
        vite_url = f"http://127.0.0.1:{vite_port}"
        environment["VITE_DEV_SERVER_URL"] = vite_url
        frontend_command = vite_command(npm, vite_port)
        frontend_display = vite_url
    print(f"Checkout: {ROOT}", flush=True)
    print(f"Frontend: {frontend_display}", flush=True)
    vite = subprocess.Popen(
        frontend_command,
        cwd=FRONTEND,
        env=environment,
        **process_creation_kwargs(),
    )
    django = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"],
        cwd=ROOT,
        env=environment,
        **process_creation_kwargs(),
    )
    try:
        while vite.poll() is None and django.poll() is None:
            time.sleep(0.25)
        return vite.returncode if vite.returncode is not None else django.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        for process in (vite, django):
            terminate_process_tree(process)


if __name__ == "__main__":
    raise SystemExit(main())
