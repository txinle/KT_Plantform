#!/usr/bin/env sh
set -eu
while true; do
    python manage.py run_export_jobs
    sleep 2
done
