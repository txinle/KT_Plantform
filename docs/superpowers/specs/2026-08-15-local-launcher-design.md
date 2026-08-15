# Portable Windows Launcher and Package Design

## Goal

Support two equivalent Windows workflows:

1. A developer double-clicks `run_local.bat` or runs `.\run_local.bat` from PowerShell.
2. A recipient who already has Python or Anaconda extracts a clean package, double-clicks `run_local.bat`, and receives a machine-local environment, database, and automatically opened website without editing paths.

The launcher must not contain a username, drive letter, Python installation directory, or project extraction directory from the source computer.

## Components

### `run_local.bat`

The batch file anchors all relative paths to its own directory with `cd /d "%~dp0"`. It reuses `.venv\Scripts\python.exe` when present. When `.venv` is absent, it calls the Python discovery helper, creates `.venv`, upgrades pip once, and installs the pinned requirements.

After dependency and database checks succeed, it prints `http://127.0.0.1:8000/`, starts a bounded hidden readiness helper, and keeps Django in the foreground. The helper opens the default browser only after the local server responds. Setup failures stop immediately and remain visible when the launcher was opened by double-click.

### `scripts/find_python.ps1`

The discovery helper returns one validated Python executable path and no other standard output. It searches independently of PowerShell profile or Conda activation, in this order:

1. a functional Windows `py` launcher;
2. a functional `python` command on PATH, rejecting the non-functional Microsoft Store alias;
3. user-level Anaconda and Miniconda installations;
4. system-level Anaconda and Miniconda installations;
5. standard per-user and system Python installation directories.

Every candidate is executed to confirm it is Python 3.10 or newer. Paths containing spaces are returned and consumed safely. If no candidate works, the helper exits non-zero and the launcher displays a concise prerequisite message.

### `scripts/create_portable_package.ps1`

The packaging helper creates a new timestamped output directory and ZIP outside the source tree. It uses a runtime allowlist rather than copying the entire working directory. The package contains the Django applications, configuration, migrations, fixtures, templates, static assets, launcher, Python discovery helper, dependency file, and recipient instructions.

The package explicitly excludes:

- `.venv`, `.git`, `.worktrees`, caches, tests, logs, and generated static output;
- `db.sqlite3`, `media`, `.env`, secrets, and local credentials;
- planning documents and unrelated workspace files.

The absence of `db.sqlite3` is intentional. On first launch Django creates a clean database, applies the checked-in migrations, and seeds only the documented demonstration accounts. The launcher runs `migrate`; it does not generate new migration files on a recipient machine.

The packaging helper writes `PORTABLE_WINDOWS_README.md` into the package with prerequisites, first-start behavior, URLs, stop instructions, and the warning not to copy `.venv` between computers.

## Startup data flow

1. Resolve the extracted project directory from the launcher location.
2. Reuse a valid project `.venv`, or discover the recipient's Python and create one.
3. Install requirements into that project `.venv`.
4. Apply checked-in migrations and seed demo data only when the database is new.
5. Print the clickable local URL.
6. Start the bounded readiness helper.
7. Run Django on `0.0.0.0:8000`; after readiness, open `http://127.0.0.1:8000/` in the recipient's default browser.

## Failure handling

- Missing or unsupported Python: show the prerequisite and do not create a partial database.
- Partial `.venv`: treat it as invalid and recreate only through an explicit, narrowly targeted cleanup path.
- Dependency or migration failure: stop before launching the browser and retain the terminal error.
- Port 8000 already in use: report the conflict instead of launching a second server.
- Browser readiness timeout: leave the Django terminal visible and retain the clickable URL.
- Package output already exists: create a distinct timestamped output rather than overwrite it.

## Verification

- Run a Python-discovery contract under a simulated Explorer PATH that does not contain the active Conda environment.
- Verify discovery through PATH and a common Anaconda location, plus the missing-Python error path.
- Verify a valid existing `.venv` is reused and dependency installation targets that interpreter.
- Start with browser opening disabled, confirm `http://127.0.0.1:8000/` returns HTTP 200, and stop the exact test process.
- Build a clean package and audit its allowlist: required runtime files present; `.venv`, database, media, `.env`, secrets, and caches absent.
- Extract the package to a path containing spaces and perform a clean first-start test.
- Run Django checks, the full test suite, static collection, and both JavaScript syntax checks.

## Non-goals

- The package does not bundle or install Python automatically.
- It remains a local development/research launcher, not a public production deployment mechanism.
- It does not configure Windows Firewall, campus routing, HTTPS, or cloud infrastructure.
- It does not transfer the source computer's accounts, experiments, student records, uploads, or secrets.
