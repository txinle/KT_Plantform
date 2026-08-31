@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:8000/"
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "REQUIREMENTS_STAMP=.venv\.requirements.sha256"
set "KT_POSTGRES_BIN=%USERPROFILE%\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin"
set "KT_POSTGRES_DATA=%USERPROFILE%\Downloads\ATOP-tooling\PostgreSQL-17.11-data"
set "POSTGRES_HOST=127.0.0.1"
set "POSTGRES_PORT=55432"
set "POSTGRES_USER=kt_user"
set "POSTGRES_PASSWORD=kt_password"
set "POSTGRES_DB=kt_platform"
set "PATH=%KT_POSTGRES_BIN%;%PATH%"
if not defined CLASS_CENTER_ENABLED set "CLASS_CENTER_ENABLED=1"
if not defined STUDENT_LEARNING_ANALYTICS_ENABLED set "STUDENT_LEARNING_ANALYTICS_ENABLED=1"

powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (
    echo [ERROR] Port 8000 is already in use. Stop the existing local server first.
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
    for /f "delims=" %%P in ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\find_python.ps1"') do set "BOOTSTRAP_PYTHON=%%P"
    if not defined BOOTSTRAP_PYTHON (
        echo [ERROR] Python 3.10 or newer was not found.
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

echo [2/4] Checking Python dependencies...
set "CURRENT_REQUIREMENTS_HASH="
set "INSTALLED_REQUIREMENTS_HASH="
for /f "delims=" %%H in ('powershell.exe -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements.txt').Hash"') do set "CURRENT_REQUIREMENTS_HASH=%%H"
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
    echo Starting the project PostgreSQL 17 instance...
    "%KT_POSTGRES_BIN%\pg_ctl.exe" -D "%KT_POSTGRES_DATA%" -l "%KT_POSTGRES_DATA%\server.log" -o "-p %POSTGRES_PORT%" -w start
    if errorlevel 1 goto :fail
)
"%VENV_PYTHON%" manage.py migrate --noinput
if errorlevel 1 goto :fail
set "DEMO_READY=1"
"%VENV_PYTHON%" manage.py shell -c "from django.contrib.auth import get_user_model; from django.db.models import Count; from apps.accounts.models import Course; from apps.question_bank.models import Concept; U=get_user_model(); required={'admin':'admin123','teacher':'teacher123','S001':'student123','S002':'student123','S003':'student123'}; bad=[name for name,password in required.items() if not (U.objects.filter(username=name,is_active=True).exists() and U.objects.get(username=name).check_password(password))]; course=Course.objects.filter(code='KT101',term='2026\u6625').first(); bank=course.question_banks.filter(name='\u6f14\u793a\u9898\u5e93').first() if course else None; mapped=bank.questions.annotate(kc_count=Count('concept_links')) if bank else None; invalid_codes=Concept.objects.filter(course=course).exclude(code__regex=r'^KC[1-9][0-9]*$').exists() if course else True; mapping_bad=(not bank or not bank.questions.count() == 93 or not bank.questions.filter(status='active').count() == 93 or mapped.filter(kc_count__lt=3).exists() or mapped.filter(kc_count__gt=4).exists() or Concept.objects.filter(course=course,parent__isnull=False).exists() or invalid_codes); raise SystemExit(1 if bad or mapping_bad else 0)"
if errorlevel 1 set "DEMO_READY=0"
if "!DEMO_READY!"=="0" (
    echo Demo accounts are missing or unusable; initializing demo data...
    "%VENV_PYTHON%" manage.py seed_demo --reset-question-bank
    if errorlevel 1 goto :fail
    "%VENV_PYTHON%" manage.py shell -c "from django.contrib.auth import get_user_model; U=get_user_model(); required={'admin':'admin123','teacher':'teacher123','S001':'student123','S002':'student123','S003':'student123'}; bad=[name for name,password in required.items() if not (U.objects.filter(username=name,is_active=True).exists() and U.objects.get(username=name).check_password(password))]; raise SystemExit(1 if bad else 0)"
    if errorlevel 1 (
        echo [ERROR] Demo accounts were not initialized correctly.
        goto :fail
    )
)

echo [4/4] Starting the local website...
echo Website: %APP_URL%
echo Ctrl+click the URL above if the browser does not open automatically.
if /I not "%KT_SKIP_BROWSER%"=="1" (
    start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='%APP_URL%'; for ($attempt = 0; $attempt -lt 60; $attempt++) { try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1 | Out-Null; Start-Process $url; exit 0 } catch { Start-Sleep -Milliseconds 500 } }; exit 1"
)
"%VENV_PYTHON%" scripts\run_dev.py
exit /b %errorlevel%

:fail
echo.
echo [ERROR] Local startup failed. Review the message above.
pause
exit /b 1
