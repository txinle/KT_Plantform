FROM python:3.13-slim AS python-app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=0
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ ./
COPY contracts/frontend-routes.json /contracts/frontend-routes.json
COPY deploy/docker/ ./docker/
RUN python manage.py collectstatic --noinput
RUN sed -i 's/\r$//' docker/*.sh && chmod +x docker/*.sh

FROM python-app AS web
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "-c", "config/gunicorn.py", "config.wsgi:application"]

FROM python-app AS scheduler
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python", "manage.py", "run_scheduler"]

FROM python-app AS analytics-worker
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python", "manage.py", "run_research_materializations"]

FROM python-app AS publication-worker
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python", "manage.py", "run_publication_jobs"]

FROM python-app AS student-comparison-worker
ENTRYPOINT ["docker/school-entrypoint.sh"]
CMD ["python", "manage.py", "run_student_comparisons"]

FROM python-app AS export-worker
ENTRYPOINT ["docker/school-entrypoint.sh"]
CMD ["docker/export-worker.sh"]
