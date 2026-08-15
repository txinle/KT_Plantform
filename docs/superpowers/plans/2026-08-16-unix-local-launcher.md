# Linux/macOS Local Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add a tested run_local.sh with Windows-launcher parity and produce a Unix-friendly .tar.gz alongside the Windows ZIP.

**Architecture:** Keep run_local.sh self-contained and Bash 3.2 compatible so it runs on standard macOS and Linux shells while remaining testable through Git Bash. Extend the existing PowerShell package builder with a focused Python tar writer that stores run_local.sh with executable mode 0755.

**Tech Stack:** Bash 3.2+, Python 3.10+, Django 5.2, PowerShell 5+, Python tarfile, Git Bash

---

### Task 1: Define and implement the Unix launcher contract

**Files:**
- Create: tests/test_portable_launchers.py
- Create: run_local.sh
- Modify: .gitattributes

- [ ] **Step 1: Write the failing contract test**

Create tests/test_portable_launchers.py:

~~~python
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
        source = (ROOT / "scripts" / "create_portable_package.ps1").read_text(encoding="utf-8")
        for token in (
            "run_local.sh",
            "create_portable_archive.py",
            "PORTABLE_UNIX_README.md",
            "PACKAGE_TAR_GZ=",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
~~~

- [ ] **Step 2: Verify RED**

Run:

~~~powershell
.\.venv\Scripts\python.exe manage.py test tests.test_portable_launchers -v 2
~~~

Expected: FAIL because run_local.sh and Unix package support do not exist.

- [ ] **Step 3: Implement run_local.sh**

Create run_local.sh:

~~~bash
#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$PROJECT_ROOT"

APP_URL="http://127.0.0.1:8000/"
VENV_DIR="$PROJECT_ROOT/.venv"
REQUIREMENTS_STAMP="$VENV_DIR/.requirements.sha256"
FIRST_RUN=0
[[ -f "$PROJECT_ROOT/db.sqlite3" ]] || FIRST_RUN=1

fail() {
    printf '\n[ERROR] Local startup failed. %s\n' "$1" >&2
    exit 1
}

port_is_in_use() {
    (exec 3<>/dev/tcp/127.0.0.1/8000) >/dev/null 2>&1
}

discover_python() {
    local candidate name
    local -a candidates=()
    for name in python3 python; do
        candidate="$(command -v "$name" 2>/dev/null || true)"
        [[ -n "$candidate" ]] && candidates+=("$candidate")
    done
    if [[ -n "${CONDA_PREFIX:-}" ]]; then
        candidates+=("$CONDA_PREFIX/bin/python" "$CONDA_PREFIX/python.exe")
    fi
    candidates+=(
        "$HOME/miniconda3/bin/python"
        "$HOME/anaconda3/bin/python"
        "$HOME/opt/anaconda3/bin/python"
        "/opt/homebrew/bin/python3"
        "/usr/local/bin/python3"
        "/usr/bin/python3"
    )
    for candidate in "${candidates[@]}"; do
        [[ -f "$candidate" ]] || continue
        if "$candidate" -c "import ensurepip, os, sys, venv; raise SystemExit(0 if sys.version_info >= (3, 10) and os.path.isfile(sys.executable) else 1)" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

find_venv_python() {
    if [[ -f "$VENV_DIR/bin/python" ]]; then
        printf '%s\n' "$VENV_DIR/bin/python"
        return 0
    fi
    if [[ -f "$VENV_DIR/Scripts/python.exe" ]]; then
        printf '%s\n' "$VENV_DIR/Scripts/python.exe"
        return 0
    fi
    return 1
}

remove_incomplete_venv() {
    [[ "$VENV_DIR" == "$PROJECT_ROOT/.venv" ]] || fail "Refusing to remove an unexpected path: $VENV_DIR"
    rm -rf -- "$VENV_DIR"
}

if port_is_in_use; then
    fail "Port 8000 is already in use. Stop the existing local server first."
fi

VENV_PYTHON="$(find_venv_python || true)"
if [[ -d "$VENV_DIR" && -z "$VENV_PYTHON" ]]; then
    printf '[1/4] Removing an incomplete local Python environment...\n'
    remove_incomplete_venv
fi

if [[ -z "$VENV_PYTHON" ]]; then
    printf '[1/4] Discovering Python and creating the local environment...\n'
    BOOTSTRAP_PYTHON="$(discover_python || true)"
    [[ -n "$BOOTSTRAP_PYTHON" ]] || fail "Python 3.10+ with venv and ensurepip was not found."
    "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR" || fail "Python could not create .venv."
    VENV_PYTHON="$(find_venv_python || true)"
    [[ -n "$VENV_PYTHON" ]] || fail "The new .venv does not contain Python."
    "$VENV_PYTHON" -m pip install --upgrade pip || fail "pip upgrade failed."
else
    printf "[1/4] Reusing this project's local Python environment.\n"
fi

printf '[2/4] Checking Python dependencies...\n'
CURRENT_REQUIREMENTS_HASH="$("$VENV_PYTHON" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$PROJECT_ROOT/requirements.txt")"
INSTALLED_REQUIREMENTS_HASH=""
[[ -f "$REQUIREMENTS_STAMP" ]] && INSTALLED_REQUIREMENTS_HASH="$(<"$REQUIREMENTS_STAMP")"
if [[ "$CURRENT_REQUIREMENTS_HASH" == "$INSTALLED_REQUIREMENTS_HASH" ]]; then
    printf 'Dependencies are unchanged; using the installed packages.\n'
else
    "$VENV_PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt" || fail "Dependency installation failed."
    printf '%s\n' "$CURRENT_REQUIREMENTS_HASH" >"$REQUIREMENTS_STAMP"
fi

printf '[3/4] Checking the database...\n'
"$VENV_PYTHON" manage.py migrate --noinput || fail "Database migration failed."
if [[ "$FIRST_RUN" == "1" ]]; then
    "$VENV_PYTHON" manage.py seed_demo || fail "Demo data initialization failed."
fi

printf '[4/4] Starting the local website...\n'
printf 'Website: %s\n' "$APP_URL"
printf 'Open the URL above if the browser does not open automatically.\n'
if [[ "${KT_SKIP_BROWSER:-0}" != "1" ]]; then
    "$VENV_PYTHON" - "$APP_URL" >/dev/null 2>&1 <<'PY' &
import socket
import sys
import time
import webbrowser

url = sys.argv[1]
for _ in range(60):
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1):
            webbrowser.open(url)
            break
    except OSError:
        time.sleep(0.5)
PY
fi

exec "$VENV_PYTHON" manage.py runserver 0.0.0.0:8000
~~~

- [ ] **Step 4: Set LF and executable mode**

Append to .gitattributes:

~~~gitattributes
*.sh text eol=lf
~~~

Run:

~~~powershell
git add .gitattributes run_local.sh
git update-index --chmod=+x run_local.sh
& 'C:\Program Files\Git\bin\bash.exe' -n .\run_local.sh
.\.venv\Scripts\python.exe manage.py test tests.test_portable_launchers.PortableLauncherContractTests.test_unix_launcher_matches_local_startup_contract -v 2
git ls-files --stage run_local.sh
~~~

Expected: syntax and contract pass; Git mode is 100755.

- [ ] **Step 5: Commit**

~~~powershell
git add .gitattributes run_local.sh tests/test_portable_launchers.py
git update-index --chmod=+x run_local.sh
git commit -m "feat: add Linux and macOS local launcher"
~~~

### Task 2: Add a Unix tar archive writer

**Files:**
- Create: scripts/create_portable_archive.py
- Modify: tests/test_portable_launchers.py

- [ ] **Step 1: Add the failing archive test**

Append these imports:

~~~python
import subprocess
import sys
import tarfile
import tempfile
~~~

Then add:

~~~python
class PortableArchiveTests(TestCase):
    def test_tar_archive_marks_unix_launcher_executable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            source.mkdir()
            (source / "run_local.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            (source / "manage.py").write_text("", encoding="utf-8")
            archive = root / "package.tar.gz"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "create_portable_archive.py"),
                    "--source", str(source),
                    "--output", str(archive),
                ],
                check=True,
            )
            with tarfile.open(archive, "r:gz") as tar:
                members = {member.name: member for member in tar.getmembers()}
            self.assertEqual(members["run_local.sh"].mode, 0o755)
            self.assertEqual(members["manage.py"].mode, 0o644)
~~~

Run the test and expect failure because the helper is missing.

- [ ] **Step 2: Implement scripts/create_portable_archive.py**

~~~python
import argparse
import tarfile
from pathlib import Path


def add_tree(source: Path, output: Path) -> None:
    paths = sorted(
        source.rglob("*"),
        key=lambda path: (len(path.relative_to(source).parts), path.as_posix()),
    )
    with tarfile.open(output, "w:gz") as archive:
        for path in paths:
            relative = path.relative_to(source).as_posix()
            info = archive.gettarinfo(str(path), arcname=relative)
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            if path.is_dir():
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.mode = 0o755 if relative == "run_local.sh" else 0o644
                with path.open("rb") as source_file:
                    archive.addfile(info, source_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    add_tree(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
~~~

- [ ] **Step 3: Verify GREEN and commit**

~~~powershell
.\.venv\Scripts\python.exe manage.py test tests.test_portable_launchers.PortableArchiveTests -v 2
git add scripts/create_portable_archive.py tests/test_portable_launchers.py
git commit -m "feat: add portable Unix archive writer"
~~~

### Task 3: Extend the package builder

**Files:**
- Modify: scripts/create_portable_package.ps1
- Test: tests/test_portable_launchers.py

- [ ] **Step 1: Add package outputs**

Add a tar path beside the ZIP path:

~~~powershell
$tarGzPath = Join-Path $outputRootFull "$packageName.tar.gz"
~~~

Include it in the collision check. Add run_local.sh to runtimeFiles. Copy create_portable_archive.py with the existing package scripts.

- [ ] **Step 2: Write PORTABLE_UNIX_README.md**

The complete content is:

~~~text
# KT Data Platform - Linux/macOS Portable Package

Requirement: Python 3.10+ with venv and ensurepip. Internet access is needed for the first dependency installation.

1. Extract the complete .tar.gz to a writable folder.
2. Open a terminal in that folder and run ./run_local.sh.
3. First start creates this computer's own .venv and a clean local database.
4. The browser opens http://127.0.0.1:8000/ after Django is ready.
5. Keep the terminal open while using the site. Press Ctrl+C to stop it.

This launcher uses Django's development server. Do not expose it directly to the public internet.
~~~

- [ ] **Step 3: Invoke the archive writer**

After Compress-Archive, add:

~~~powershell
$finderOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'find_python.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.10+ is required to create the Unix archive.'
}
$archivePython = @($finderOutput)[0].Trim()
& $archivePython (Join-Path $PSScriptRoot 'create_portable_archive.py') --source $packageDirectory --output $tarGzPath
if ($LASTEXITCODE -ne 0) {
    throw 'Unix archive creation failed.'
}
Write-Output "PACKAGE_TAR_GZ=$tarGzPath"
~~~

- [ ] **Step 4: Verify and commit**

~~~powershell
.\.venv\Scripts\python.exe manage.py test tests.test_portable_launchers -v 2
[void][ScriptBlock]::Create((Get-Content -Raw .\scripts\create_portable_package.ps1))
git diff --check
git add scripts/create_portable_package.ps1 tests/test_portable_launchers.py
git commit -m "feat: package Linux and macOS launcher"
~~~

### Task 4: End-to-end verification and merge

**Files:**
- Verify: generated ZIP and tar.gz
- Verify: repository and Git history

- [ ] **Step 1: Build and audit packages**

Build to a temporary output path containing spaces. Require PACKAGE_DIR, PACKAGE_ZIP, and PACKAGE_TAR_GZ. Inspect the tar with Python tarfile and require run_local.sh mode 0755; reject .venv, db.sqlite3, .env, media, tests, and caches.

- [ ] **Step 2: Perform clean Git Bash first start**

Extract the tar to a new path containing spaces. Verify port 8000 is free. Start from that directory with:

~~~powershell
$env:KT_SKIP_BROWSER = '1'
& 'C:\Program Files\Git\bin\bash.exe' ./run_local.sh
~~~

In a managed session, wait for the URL, require HTTP 200 and creation of .venv, db.sqlite3, and .venv/.requirements.sha256, then stop only that session.

- [ ] **Step 3: Verify second-start reuse**

Run the extracted launcher again. Require Dependencies are unchanged; using the installed packages. and reject Collecting Django. Stop only the managed session.

- [ ] **Step 4: Run full regression**

~~~powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
node --check static\js\app.js
node --check static\js\assessment.js
& 'C:\Program Files\Git\bin\bash.exe' -n .\run_local.sh
git diff --check
git ls-files --stage run_local.sh
~~~

Expected: at least 50 tests pass, syntax checks pass, and mode is 100755.

- [ ] **Step 5: Merge and push main**

Fetch origin; require origin/main to be an ancestor of local main and verify the feature branch does not modify README.md. Record the README SHA-256, fast-forward merge feature/unix-local-launcher into main, re-run Step 4 on merged main, verify the README hash is unchanged, push main, and require local main to equal origin/main.

- [ ] **Step 6: Clean owned isolation**

Remove only .worktrees/unix-local-launcher, prune worktrees, and delete the merged local feature branch. Preserve the user's uncommitted README.md modification.
