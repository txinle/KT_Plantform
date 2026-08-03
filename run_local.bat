@echo off
setlocal
cd /d %~dp0
set FIRST_RUN=0
if not exist db.sqlite3 set FIRST_RUN=1
if not exist .venv py -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py makemigrations accounts question_bank experiments assessment audit --noinput
python manage.py migrate
if "%FIRST_RUN%"=="1" python manage.py seed_demo
python manage.py runserver 0.0.0.0:8000
