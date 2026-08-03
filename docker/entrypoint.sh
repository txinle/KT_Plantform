#!/usr/bin/env sh
set -e
python manage.py makemigrations accounts question_bank experiments assessment audit --noinput
python manage.py migrate --noinput
python manage.py collectstatic --noinput
if [ "${SEED_DEMO:-0}" = "1" ]; then python manage.py seed_demo; fi
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout 60
