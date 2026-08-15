# Local Launcher Design

## Goal

Make `run_local.bat` reliable when launched either by double-clicking it or by running `\.\run_local.bat` in PowerShell. After startup checks succeed and the Django server becomes reachable, the default browser opens `http://127.0.0.1:8000/`. The terminal also prints that URL so it can be opened with Ctrl+click.

## Startup flow

1. Change to the project directory and define the local URL.
2. Reuse `.venv\Scripts\python.exe` when it exists.
3. When the virtual environment is missing, locate `python`, create `.venv`, upgrade pip once, and stop immediately with a clear message if any step fails.
4. Install the pinned requirements, run migration generation and migrations, and seed demo data only for a new database. Each command must stop the launcher on failure.
5. Print the clickable local URL.
6. Start one hidden PowerShell helper that polls the local URL. It opens the default browser only after Django responds, then exits. Polling is bounded so a failed server does not leave a background process running indefinitely.
7. Run Django on `0.0.0.0:8000` in the foreground so logs remain visible and Ctrl+C/CTRL-BREAK can stop it.

## Failure handling

- Missing Python, virtual-environment creation failure, dependency failure, or migration failure prevents server startup and browser launch.
- The launcher pauses on setup failure when started by double-click, so the error remains readable.
- A browser helper timeout does not terminate an otherwise running Django server; the printed URL remains available.
- The change remains Windows-only. `run_local.sh` is unaffected.

## Verification

- A static regression check verifies that the launcher uses the virtual-environment interpreter, does not depend on the unavailable `py` launcher, prints the local URL, launches a bounded readiness poll, and guards setup commands.
- Run the launcher with automatic browser opening disabled through a test-only environment flag, confirm the server responds at the local URL, then stop it.
- Run Django's project checks and existing test suite to detect regressions.

## Non-goals

- This remains a local development launcher, not a production deployment mechanism.
- It does not install Python automatically, configure Windows Firewall, reserve a campus IP address, or add HTTPS.
