from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_DIR = BASE_DIR.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-development-key-change-before-production")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED = os.getenv('SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED', '0') == '1'
ASSESSMENT_CONNECT_RETRY_ENABLED = os.getenv('ASSESSMENT_CONNECT_RETRY_ENABLED', '0') == '1'
CAPTURE_V2_ENABLED = os.getenv('CAPTURE_V2_ENABLED', '0') == '1'
CAPTURE_BUNDLE_ENABLED = os.getenv('CAPTURE_BUNDLE_ENABLED', '1') == '1'
ASSESSMENT_ADMISSION_ENABLED = os.getenv('ASSESSMENT_ADMISSION_ENABLED', '0') == '1'
ASSESSMENT_ADMISSION_TASK_IDS = frozenset(
    filter(None, os.getenv('ASSESSMENT_ADMISSION_TASK_IDS', '').split(','))
)
ASSESSMENT_ADMISSION_REDIS_URL = os.getenv(
    'ASSESSMENT_ADMISSION_REDIS_URL', 'redis://redis-admission:6379/0'
)
ASSESSMENT_ADMISSION_CAPACITY = int(os.getenv('ASSESSMENT_ADMISSION_CAPACITY', '8'))
ASSESSMENT_ADMISSION_LEASE_SECONDS = int(
    os.getenv('ASSESSMENT_ADMISSION_LEASE_SECONDS', '180')
)
ASSESSMENT_ADMISSION_TTL_SECONDS = int(
    os.getenv('ASSESSMENT_ADMISSION_TTL_SECONDS', '900')
)
ATOMIC_SUBMISSION_ENABLED = os.getenv('ATOMIC_SUBMISSION_ENABLED', '0') == '1'
ATOMIC_SUBMISSION_USER_IDS = frozenset(filter(None, os.getenv('ATOMIC_SUBMISSION_USER_IDS', '').split(',')))
ATOMIC_SUBMISSION_COURSE_IDS = frozenset(filter(None, os.getenv('ATOMIC_SUBMISSION_COURSE_IDS', '').split(',')))
SAVE_AND_LEAVE_ENABLED = os.getenv('SAVE_AND_LEAVE_ENABLED', '0') == '1'
SAVE_AND_LEAVE_USER_IDS = frozenset(filter(None, os.getenv('SAVE_AND_LEAVE_USER_IDS', '').split(',')))
SAVE_AND_LEAVE_COURSE_IDS = frozenset(filter(None, os.getenv('SAVE_AND_LEAVE_COURSE_IDS', '').split(',')))
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
STUDENT_SHARED_RESULTS_ENABLED = os.getenv("STUDENT_SHARED_RESULTS_ENABLED", "0") == "1"
STUDENT_SHARED_RESULTS_COURSE_IDS = frozenset(
    filter(None, os.getenv("STUDENT_SHARED_RESULTS_COURSE_IDS", "").split(","))
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
    "apps.accounts.middleware.ResearchConsentRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
CSRF_FAILURE_VIEW = "apps.api.exceptions.csrf_failure"
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
        "NAME": os.getenv("POSTGRES_POOL_ALIAS") or os.getenv("POSTGRES_DB", "kt_platform"),
        "USER": os.getenv("POSTGRES_USER", "kt_user"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "kt_password"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DATABASE_CONN_MAX_AGE", "0")),
        "CONN_HEALTH_CHECKS": True,
        "DISABLE_SERVER_SIDE_CURSORS": os.getenv("DATABASE_DISABLE_SERVER_SIDE_CURSORS", "0") == "1",
        "OPTIONS": {
            "application_name": os.getenv("DATABASE_APPLICATION_NAME", "kt-web")
        },
        "TEST": {"NAME": os.getenv("POSTGRES_TEST_DB")},
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
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_SECURE = os.getenv("DJANGO_SESSION_COOKIE_SECURE", "0" if DEBUG else "1") == "1"
CSRF_COOKIE_SECURE = os.getenv("DJANGO_CSRF_COOKIE_SECURE", "0" if DEBUG else "1") == "1"
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "0") == "1"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
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
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.api.schema.envelope_responses",
    ],
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
