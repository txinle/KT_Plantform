#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
FIRST_RUN=0
if [ ! -f db.sqlite3 ]; then FIRST_RUN=1; fi
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python manage.py makemigrations accounts question_bank experiments assessment audit --noinput
python manage.py migrate
if [ "$FIRST_RUN" = "1" ]; then python manage.py seed_demo; fi
python manage.py runserver 0.0.0.0:8000
