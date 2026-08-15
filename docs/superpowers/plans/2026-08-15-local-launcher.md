# Reliable Local Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_local.bat` create and use its virtual environment reliably, print a clickable local URL, and open the default browser only after Django responds.

**Architecture:** Keep the launcher as one Windows batch file. A hidden, bounded PowerShell readiness helper polls `http://127.0.0.1:8000/` while Django remains in the foreground; `KT_SKIP_BROWSER=1` suppresses only the browser helper during automated verification.

**Tech Stack:** Windows batch, PowerShell 5+, Python virtual environments, Django 5.2

---

### Task 1: Make the Windows launcher reliable and browser-aware

**Files:**
- Modify: `run_local.bat:1-13`

- [x] **Step 1: Run a failing launcher contract test**

Run this PowerShell assertion against the current batch file:

```powershell
$script = Get-Content -Raw .\run_local.bat
$checks = [ordered]@{
    no_py_launcher = $script -notmatch '(?m)^.*\bpy -m venv\b'
    venv_python = $script -match 'VENV_PYTHON'
    clickable_url = $script -match 'Website: %APP_URL%'
    readiness_poll = $script -match 'Invoke-WebRequest' -and $script -match 'Start-Process \$url'
    browser_test_switch = $script -match 'KT_SKIP_BROWSER'
    guarded_setup = $script -match 'if errorlevel 1 goto :fail'
}
$failed = @($checks.GetEnumerator() | Where-Object { -not $_.Value } | ForEach-Object Key)
if ($failed.Count) { throw "Missing launcher behavior: $($failed -join ', ')" }
```

Expected: FAIL and list the missing launcher behavior, proving the current script does not meet the approved contract.

- [x] **Step 2: Replace the launcher with the minimal implementation**

Replace `run_local.bat` with:

```bat
@echo off
setlocal
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:8000/"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "FIRST_RUN=0"
if not exist db.sqlite3 set "FIRST_RUN=1"

if not exist "%VENV_PYTHON%" (
    echo [1/4] Creating the local Python environment...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found on PATH.
        goto :fail
    )
    python -m venv .venv
    if errorlevel 1 goto :fail
    "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :fail
)

echo [2/4] Checking Python dependencies...
"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/4] Checking the database...
"%VENV_PYTHON%" manage.py makemigrations accounts question_bank experiments assessment audit --noinput
if errorlevel 1 goto :fail
"%VENV_PYTHON%" manage.py migrate
if errorlevel 1 goto :fail
if "%FIRST_RUN%"=="1" (
    "%VENV_PYTHON%" manage.py seed_demo
    if errorlevel 1 goto :fail
)

echo [4/4] Starting the local website...
echo Website: %APP_URL%
echo Ctrl+click the URL above if the browser does not open automatically.
if /I not "%KT_SKIP_BROWSER%"=="1" (
    start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='%APP_URL%'; for ($attempt = 0; $attempt -lt 60; $attempt++) { try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1 | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1"
)
"%VENV_PYTHON%" manage.py runserver 0.0.0.0:8000
exit /b %errorlevel%

:fail
echo.
echo [ERROR] Local startup failed. Review the message above.
pause
exit /b 1
```

- [x] **Step 3: Re-run the launcher contract test**

Run the Step 1 PowerShell assertion again.

Expected: PASS with exit code `0` and no missing behavior.

- [x] **Step 4: Run the launcher without opening a browser**

Run:

```powershell
$env:KT_SKIP_BROWSER = '1'
.\run_local.bat
```

Expected terminal evidence:

```text
[4/4] Starting the local website...
Website: http://127.0.0.1:8000/
Starting development server at http://0.0.0.0:8000/
```

From a second PowerShell window, run:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/).StatusCode
```

Expected: `200`. Stop the launcher with Ctrl+C and clear `$env:KT_SKIP_BROWSER`.

- [x] **Step 5: Run project regression checks**

Run:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

Expected: Django check reports no issues, all tests pass, and static collection exits with code `0`.

- [x] **Step 6: Review the focused diff**

Run:

```powershell
git diff --check -- run_local.bat
git diff -- run_local.bat
```

Expected: no whitespace errors; the diff is limited to the approved launcher behavior.

- [x] **Step 7: Commit the launcher change**

Run:

```powershell
git add run_local.bat
git commit -m "fix: make local launcher open the website"
```
