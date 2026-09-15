FROM node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY static/js/ /static/js/
RUN npm run build

FROM python:3.13-slim AS python-app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=0
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
RUN python manage.py collectstatic --noinput
RUN chmod +x docker/entrypoint.sh

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

FROM nginx:1.27-alpine AS nginx
COPY nginx/kt-platform.conf /etc/nginx/conf.d/default.conf
COPY --from=python-app /app/staticfiles /static
COPY --from=frontend-build /frontend/dist/spa-assets /static/spa-assets
COPY --from=frontend-build /frontend/dist/spa-assets/assets /opt/kt-frontend-assets
COPY docker/20-frontend-assets.sh /docker-entrypoint.d/20-frontend-assets.sh
RUN sed -i 's/\r$//' /docker-entrypoint.d/20-frontend-assets.sh && chmod +x /docker-entrypoint.d/20-frontend-assets.sh
