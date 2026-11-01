@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0server"

set "APP_URL=http://127.0.0.1:8000/"
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
set "REQUIREMENTS_STAMP=%~dp0.venv\.requirements.sha256"
if not defined KT_TOOLING_ROOT set "KT_TOOLING_ROOT=%LOCALAPPDATA%\KT——platform\runtime"
if not defined POSTGRES_HOST set "POSTGRES_HOST=127.0.0.1"
if not defined POSTGRES_PORT set "POSTGRES_PORT=55432"
if not defined POSTGRES_USER set "POSTGRES_USER=kt_user"
if not defined POSTGRES_PASSWORD set "POSTGRES_PASSWORD=kt_password"
if not defined POSTGRES_DB set "POSTGRES_DB=kt_platform"
if not defined CLASS_CENTER_ENABLED set "CLASS_CENTER_ENABLED=1"
if not defined STUDENT_LEARNING_ANALYTICS_ENABLED set "STUDENT_LEARNING_ANALYTICS_ENABLED=1"

powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; try { $owners=@(Get-NetTCPConnection -LocalPort 8000,8001 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); foreach ($owner in $owners) { Write-Host ('[INFO] Stopping process '+$owner+' using port 8000 or 8001...'); Get-Process -Id $owner -ErrorAction SilentlyContinue | Stop-Process -Force }; for ($attempt=0; $attempt -lt 30; $attempt++) { if (-not (Get-NetTCPConnection -LocalPort 8000,8001 -State Listen -ErrorAction SilentlyContinue)) { exit 0 }; Start-Sleep -Milliseconds 200 }; throw 'Ports 8000 or 8001 could not be released.' } catch { Write-Host ('[ERROR] '+$_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo [ERROR] Could not stop the old server. Check process permissions or run as administrator.
    goto :fail
)

echo [0/4] Preparing compatible local runtimes...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0server\scripts\prepare_local_runtime.ps1" -RuntimeRoot "%KT_TOOLING_ROOT%"
if errorlevel 1 goto :fail
call "%KT_TOOLING_ROOT%\environment.cmd"
set "PATH=%KT_NODE_DIR%;%KT_POSTGRES_BIN%;%PATH%"
set "PIP_CACHE_DIR=%KT_TOOLING_ROOT%\cache\pip"
set "npm_config_cache=%KT_TOOLING_ROOT%\cache\npm"
set "TEMP=%KT_TOOLING_ROOT%\cache\temp"
set "TMP=%TEMP%"
set "PLAYWRIGHT_BROWSERS_PATH=%KT_TOOLING_ROOT%\pw-fresh-browsers"
echo [1/4] Using this project's root Python environment.
"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)"
if errorlevel 1 (
    echo [ERROR] This checkout requires a Python 3.13 virtual environment.
    goto :fail
)

echo [2/4] Checking Python dependencies...
set "CURRENT_REQUIREMENTS_HASH="
set "INSTALLED_REQUIREMENTS_HASH="
for /f "delims=" %%H in ('powershell.exe -NoProfile -Command "[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes('requirements.txt'))).Replace('-','')"') do set "CURRENT_REQUIREMENTS_HASH=%%H"
if not defined CURRENT_REQUIREMENTS_HASH goto :fail
if exist "%REQUIREMENTS_STAMP%" set /p "INSTALLED_REQUIREMENTS_HASH="<"%REQUIREMENTS_STAMP%"
if /I "!CURRENT_REQUIREMENTS_HASH!"=="!INSTALLED_REQUIREMENTS_HASH!" (
    echo Dependencies are unchanged; using the installed packages.
) else (
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
    >"%REQUIREMENTS_STAMP%" echo !CURRENT_REQUIREMENTS_HASH!
)

echo [3/4] Checking the database...
"%KT_POSTGRES_BIN%\pg_isready.exe" -h %POSTGRES_HOST% -p %POSTGRES_PORT% -U %POSTGRES_USER% >nul 2>&1
if errorlevel 1 goto :fail
"%VENV_PYTHON%" manage.py migrate --noinput
if errorlevel 1 goto :fail
"%VENV_PYTHON%" manage.py shell -c "from django.contrib.auth import get_user_model; from django.core.management import call_command; call_command('seed_demo') if not get_user_model().objects.exists() else None"
if errorlevel 1 goto :fail

echo [4/4] Starting the local website...
echo Website: %APP_URL%
echo Ctrl+click the URL above if the browser does not open automatically.
"%VENV_PYTHON%" scripts\run_dev.py
if errorlevel 1 goto :fail
exit /b %errorlevel%

:fail
echo.
echo [ERROR] Local startup failed. Review the message above.
pause
exit /b 1
