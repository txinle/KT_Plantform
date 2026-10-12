@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0server"

set "APP_URL=http://127.0.0.1:8000/"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "REQUIREMENTS_STAMP=.venv\.requirements.sha256"
if not defined KT_TOOLING_ROOT set "KT_TOOLING_ROOT=E:\ATOP\ATOP-tooling"
if not defined KT_NODE_DIR set "KT_NODE_DIR=%KT_TOOLING_ROOT%\node-v24.20.0-win-x64"
set "KT_POSTGRES_BIN=%KT_TOOLING_ROOT%\PostgreSQL-17.11-portable\pgsql\bin"
set "KT_POSTGRES_DATA=%KT_TOOLING_ROOT%\PostgreSQL-17.11-data"
if not defined POSTGRES_HOST set "POSTGRES_HOST=127.0.0.1"
if not defined POSTGRES_PORT set "POSTGRES_PORT=55432"
if not defined POSTGRES_USER set "POSTGRES_USER=kt_user"
if not defined POSTGRES_PASSWORD set "POSTGRES_PASSWORD=kt_password"
if not defined POSTGRES_DB set "POSTGRES_DB=kt_platform"
set "PATH=%KT_NODE_DIR%;%KT_POSTGRES_BIN%;%PATH%"
set "PIP_CACHE_DIR=%KT_TOOLING_ROOT%\cache\pip"
set "npm_config_cache=%KT_TOOLING_ROOT%\cache\npm"
set "TEMP=%KT_TOOLING_ROOT%\cache\temp"
set "TMP=%TEMP%"
set "PLAYWRIGHT_BROWSERS_PATH=%KT_TOOLING_ROOT%\pw-fresh-browsers"
if not exist "%TEMP%" mkdir "%TEMP%"
if errorlevel 1 goto :fail
if not defined CLASS_CENTER_ENABLED set "CLASS_CENTER_ENABLED=1"
if not defined STUDENT_LEARNING_ANALYTICS_ENABLED set "STUDENT_LEARNING_ANALYTICS_ENABLED=1"

powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000,8001 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Port 8000 or 8001 is already in use. Stop the existing local server first.
    goto :fail
)

if exist ".venv" if not exist "%VENV_PYTHON%" (
    echo [1/4] Removing an incomplete local Python environment...
    rmdir /s /q ".venv"
    if errorlevel 1 goto :fail
)

if not exist "%VENV_PYTHON%" (
    echo [1/4] Discovering Python and creating the local environment...
    set "BOOTSTRAP_PYTHON="
    for /f "delims=" %%P in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0server\scripts\find_python.ps1"') do set "BOOTSTRAP_PYTHON=%%P"
    if not defined BOOTSTRAP_PYTHON (
        echo [ERROR] Python 3.13 was not found.
        echo Install Python or Anaconda, then double-click run_local.bat again.
        goto :fail
    )
    "!BOOTSTRAP_PYTHON!" -m venv .venv
    if errorlevel 1 goto :fail
    "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :fail
) else (
    echo [1/4] Reusing this project's local Python environment.
)
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
if not exist "%KT_POSTGRES_BIN%\pg_isready.exe" (
    echo [ERROR] Project PostgreSQL 17 tooling was not found at:
    echo %KT_POSTGRES_BIN%
    goto :fail
)
if not exist "%KT_POSTGRES_DATA%\PG_VERSION" (
    echo [ERROR] Project PostgreSQL data directory is not initialized at:
    echo %KT_POSTGRES_DATA%
    goto :fail
)
"%KT_POSTGRES_BIN%\pg_isready.exe" -h %POSTGRES_HOST% -p %POSTGRES_PORT% -U %POSTGRES_USER% >nul 2>&1
if errorlevel 1 (
    if not "%POSTGRES_HOST%:%POSTGRES_PORT%"=="127.0.0.1:55432" (
        echo [ERROR] The configured PostgreSQL server is unavailable. Start it before retrying.
        goto :fail
    )
    echo Starting the project PostgreSQL 17 instance...
    "%KT_POSTGRES_BIN%\pg_ctl.exe" -D "%KT_POSTGRES_DATA%" -l "%KT_POSTGRES_DATA%\server.log" -o "-p %POSTGRES_PORT%" -w start
    if errorlevel 1 goto :fail
)
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
