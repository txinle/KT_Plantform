from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-development-key-change-before-production")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
CAPTURE_V2_ENABLED = os.getenv('CAPTURE_V2_ENABLED', '0') == '1'
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
CAPTURE_V2_USER_IDS = frozenset(filter(None, os.getenv('CAPTURE_V2_USER_IDS', '').split(',')))
CAPTURE_V2_COURSE_IDS = frozenset(filter(None, os.getenv('CAPTURE_V2_COURSE_IDS', '').split(',')))
ADVANCED_DATA_MATERIALIZED_ENABLED = os.getenv(
    "ADVANCED_DATA_MATERIALIZED_ENABLED", "0"
) == "1"
ADVANCED_DATA_MATERIALIZED_COURSE_IDS = frozenset(
    filter(None, os.getenv("ADVANCED_DATA_MATERIALIZED_COURSE_IDS", "").split(","))
)
CANONICAL_PUBLIC_ORIGIN = os.getenv("CANONICAL_PUBLIC_ORIGIN", "").strip().rstrip("/")
STUDENT_SESSION_IDLE_TIMEOUT_SECONDS = int(
    os.getenv("STUDENT_SESSION_IDLE_TIMEOUT_SECONDS", "1800")
)
CLASS_CENTER_ENABLED = os.getenv("CLASS_CENTER_ENABLED") == "1"
STUDENT_LEARNING_ANALYTICS_ENABLED = (
    CLASS_CENTER_ENABLED
    and os.getenv("STUDENT_LEARNING_ANALYTICS_ENABLED") == "1"
)
ALLOWED_HOSTS = [x.strip() for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.api",
    "apps.accounts",
    "apps.question_bank",
    "apps.experiments",
    "apps.assessment",
    "apps.analytics",
    "apps.audit",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.api.middleware.RequestIdMiddleware",
    "apps.accounts.middleware.CanonicalPublicOriginMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.StudentSessionIdleTimeoutMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("POSTGRES_HOST"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "kt_platform"),
        "USER": os.getenv("POSTGRES_USER", "kt_user"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "kt_password"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SQLITE_PATH", BASE_DIR / "db.sqlite3"),
    }}

AUTH_PASSWORD_VALIDATORS = [] if DEBUG else [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = os.getenv("TIME_ZONE", "Asia/Shanghai")
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
VITE_DEV_SERVER_URL = os.getenv(
    "VITE_DEV_SERVER_URL",
    "http://localhost:5173" if DEBUG else "",
).rstrip("/")
VITE_MANIFEST_PATH = BASE_DIR / "frontend" / "dist" / "spa-assets" / ".vite" / "manifest.json"
VITE_ASSET_ROOT = BASE_DIR / "frontend" / "dist" / "spa-assets"
if VITE_ASSET_ROOT.exists():
    STATICFILES_DIRS.append(("spa-assets", VITE_ASSET_ROOT))
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "0") == "1"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.authentication.ApiSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.FixedPageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "KT Data Lab API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
