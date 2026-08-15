# Linux/macOS Local Launcher Design

## Goal

Add `run_local.sh` with the same local-development behavior as `run_local.bat`, package it for Linux/macOS, test it with the available Git Bash environment, and merge it into `main` after all checks pass.

## Scope

This change adds local startup support only. It does not add Gunicorn configuration, Nginx, systemd, domains, HTTPS, cloud deployment, or other production infrastructure.

## Launcher behavior

`run_local.sh` will:

1. Resolve the project root from the script's own location and run from that directory.
2. Refuse to start when port 8000 is already occupied.
3. Discover a usable Python 3.10+ interpreter from the active environment, `python3`, `python`, common Anaconda/Miniconda locations, and common macOS/Linux installation paths.
4. Reject embedded or incomplete Python runtimes unless `venv`, `ensurepip`, and a real `sys.executable` file are available.
5. Create a project-local `.venv` on first start and reuse it later. Native Linux/macOS uses `.venv/bin/python`; Git Bash test compatibility may use `.venv/Scripts/python.exe`.
6. Hash `requirements.txt`, install dependencies only on first start or after the requirements file changes, and store the hash inside `.venv`.
7. Run `manage.py migrate --noinput`, then run `manage.py seed_demo` only when `db.sqlite3` did not exist before startup.
8. Print `http://127.0.0.1:8000/`, wait until the server is listening, and open the system browser through Python's standard `webbrowser` module. `KT_SKIP_BROWSER=1` disables browser opening during automated tests.
9. Run Django's development server in the foreground so Ctrl+C stops it.

Failures will stop immediately, print a clear error, and return a non-zero exit code. An incomplete project-local `.venv` may be removed only after its resolved path is verified as the project's exact `.venv` directory.

## Distribution

The existing Windows package builder will include both `run_local.bat` and `run_local.sh` while continuing to exclude `.venv`, databases, uploads, secrets, tests, caches, and workspace files.

It will produce:

- the existing ZIP for Windows recipients;
- a `.tar.gz` for Linux/macOS recipients, with `run_local.sh` stored as executable;
- recipient instructions covering `run_local.bat` and `./run_local.sh`.

The Git index will also record `run_local.sh` as executable so Git clones preserve direct execution permission.

## Verification

Available local verification will include:

- `bash -n run_local.sh` using Git Bash;
- a clean first start from a path containing spaces with an Explorer-like PATH;
- confirmation that `.venv`, `db.sqlite3`, and the dependency stamp are created;
- HTTP 200 from the running site;
- a second start proving dependencies are reused rather than installed again;
- ZIP and `.tar.gz` content audits, including tar mode `0755` for `run_local.sh`;
- Django system check, all Django tests, static collection, and JavaScript syntax checks;
- post-merge verification on `main` before pushing remote `main`.

This Windows host has Git Bash but no WSL, Docker, or macOS runner. Passing these checks establishes shell compatibility and end-to-end behavior in Git Bash; a real Linux host remains required before claiming production Linux deployment readiness.
