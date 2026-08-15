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
    if [[ -f "$PROJECT_ROOT/.venv/bin/python" ]]; then
        printf '%s\n' "$PROJECT_ROOT/.venv/bin/python"
        return 0
    fi
    if [[ -f "$PROJECT_ROOT/.venv/Scripts/python.exe" ]]; then
        printf '%s\n' "$PROJECT_ROOT/.venv/Scripts/python.exe"
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
