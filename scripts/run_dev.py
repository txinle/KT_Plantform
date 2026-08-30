import hashlib
import os
from pathlib import Path
import shutil
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


def main():
    legacy_only = os.getenv("KT_LEGACY_ONLY") == "1"
    environment = os.environ.copy()
    if legacy_only:
        environment["VUE_TEACHER_EXPERIMENTS_ENABLED"] = "0"
        return subprocess.call(
            [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"],
            cwd=ROOT,
            env=environment,
        )

    environment["VUE_TEACHER_EXPERIMENTS_ENABLED"] = "1"
    node_directory, npm = find_node_directory()
    environment["PATH"] = f"{node_directory}{os.pathsep}{environment.get('PATH', '')}"
    environment["VITE_DEV_SERVER_URL"] = "http://localhost:5173"
    ensure_frontend_dependencies(npm)
    hidden = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    vite = subprocess.Popen(
        [str(npm), "run", "dev", "--", "--host", "127.0.0.1"],
        cwd=FRONTEND,
        env=environment,
        creationflags=hidden,
    )
    django = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", "0.0.0.0:8000"],
        cwd=ROOT,
        env=environment,
    )
    try:
        while vite.poll() is None and django.poll() is None:
            time.sleep(0.25)
        return vite.returncode if vite.returncode is not None else django.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        for process in (vite, django):
            if process.poll() is None:
                process.terminate()
        for process in (vite, django):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
