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
