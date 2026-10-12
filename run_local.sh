#!/usr/bin/env bash
set -Eeuo pipefail

LAUNCHER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        export MSYS_NO_PATHCONV=1
        exec cmd.exe /d /c "$(cygpath -w "$LAUNCHER_ROOT/run_local.bat")"
        ;;
esac

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/server" && pwd -P)"
cd "$PROJECT_ROOT"

APP_URL="http://127.0.0.1:8000/"
VENV_DIR="$PROJECT_ROOT/.venv"
REQUIREMENTS_STAMP="$VENV_DIR/.requirements.sha256"
: "${CLASS_CENTER_ENABLED:=1}"
: "${STUDENT_LEARNING_ANALYTICS_ENABLED:=1}"
export CLASS_CENTER_ENABLED STUDENT_LEARNING_ANALYTICS_ENABLED
: "${POSTGRES_HOST:=127.0.0.1}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:=kt_user}"
: "${POSTGRES_PASSWORD:=kt_password}"
: "${POSTGRES_DB:=kt_platform}"
export POSTGRES_HOST POSTGRES_PORT POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB
LOCAL_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/kt-data-lab"
export PIP_CACHE_DIR="$LOCAL_CACHE/pip" npm_config_cache="$LOCAL_CACHE/npm"
export TMPDIR="$LOCAL_CACHE/tmp"
mkdir -p "$TMPDIR"

fail() {
    printf '\n[ERROR] Local startup failed. %s\n' "$1" >&2
    exit 1
}

port_is_in_use() {
    (exec 3<>/dev/tcp/127.0.0.1/"$1") >/dev/null 2>&1
}

discover_python() {
    local candidate name
    local -a candidates=()
    for name in python3.13 python3 python; do
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
        if "$candidate" -c "import ensurepip, os, sys, venv; raise SystemExit(0 if sys.version_info[:2] == (3, 13) and os.path.isfile(sys.executable) else 1)" >/dev/null 2>&1; then
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

for port in 8000 8001; do
    if port_is_in_use "$port"; then
        fail "Port $port is already in use. Stop the existing local server first."
    fi
done
command -v pg_isready >/dev/null || fail "PostgreSQL 17 tools must be on PATH."
pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" >/dev/null || fail "Start the configured PostgreSQL server before retrying."

VENV_PYTHON="$(find_venv_python || true)"
if [[ -d "$VENV_DIR" && -z "$VENV_PYTHON" ]]; then
    printf '[1/4] Removing an incomplete local Python environment...\n'
    remove_incomplete_venv
fi

if [[ -z "$VENV_PYTHON" ]]; then
    printf '[1/4] Discovering Python and creating the local environment...\n'
    BOOTSTRAP_PYTHON="$(discover_python || true)"
    [[ -n "$BOOTSTRAP_PYTHON" ]] || fail "Python 3.13 with venv and ensurepip was not found."
    "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR" || fail "Python could not create .venv."
    VENV_PYTHON="$(find_venv_python || true)"
    [[ -n "$VENV_PYTHON" ]] || fail "The new .venv does not contain Python."
    "$VENV_PYTHON" -m pip install --upgrade pip || fail "pip upgrade failed."
else
    printf "[1/4] Reusing this project's local Python environment.\n"
fi
"$VENV_PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" || fail "This checkout requires a Python 3.13 virtual environment."

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
"$VENV_PYTHON" manage.py shell -c "from django.contrib.auth import get_user_model; from django.core.management import call_command; call_command('seed_demo') if not get_user_model().objects.exists() else None" || fail "Empty database initialization failed."

printf '[4/4] Starting the local website...\n'
printf 'Website: %s\n' "$APP_URL"
printf 'Open the URL above if the browser does not open automatically.\n'
exec "$VENV_PYTHON" scripts/run_dev.py
