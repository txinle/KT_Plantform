FROM node:24-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS python-app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=0
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
COPY --from=frontend-build /frontend/dist ./frontend/dist
RUN python manage.py check_vue_assets
RUN python manage.py collectstatic --noinput
RUN chmod +x docker/entrypoint.sh

FROM python-app AS web
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]

FROM python-app AS scheduler
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["python", "manage.py", "run_scheduler"]

FROM nginx:1.27-alpine AS nginx
COPY nginx/kt-platform.conf /etc/nginx/conf.d/default.conf
COPY --from=python-app /app/staticfiles /static
