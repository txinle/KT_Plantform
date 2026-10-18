from pathlib import Path
import os
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_DIR = BASE_DIR.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-development-key-change-before-production")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED = os.getenv('SCHOOL_CAPTURE_DIAGNOSTICS_ENABLED', '0') == '1'
ASSESSMENT_REQUEST_TIMING_ENABLED = os.getenv('ASSESSMENT_REQUEST_TIMING_ENABLED', '0') == '1'
SCHOOL_REDIS_SESSION_ENABLED = os.getenv('SCHOOL_REDIS_SESSION_ENABLED', '0') == '1'
SESSION_ENGINE = 'apps.accounts.database_sessions'
SESSION_REDIS_URL = os.getenv('SESSION_REDIS_URL', 'redis://redis-session:6379/0')
SESSION_REDIS_KEY_PREFIX = os.getenv('SESSION_REDIS_KEY_PREFIX', 'kt:school:session:v1:')
SESSION_REDIS_TIMEOUT_SECONDS = float(os.getenv('SESSION_REDIS_TIMEOUT_SECONDS', '1'))
if SCHOOL_REDIS_SESSION_ENABLED:
    if os.getenv('SCHOOL_RUNTIME_ISOLATION_ENABLED') != '1' or os.getenv('POSTGRES_DB') != 'kt_e2e':
        raise ImproperlyConfigured('Redis sessions require the explicit isolated school candidate database kt_e2e')
    SESSION_ENGINE = 'apps.accounts.redis_sessions'
ASSESSMENT_CONNECT_RETRY_ENABLED = os.getenv('ASSESSMENT_CONNECT_RETRY_ENABLED', '0') == '1'
CAPTURE_V2_ENABLED = os.getenv('CAPTURE_V2_ENABLED', '0') == '1'
CAPTURE_STABLE_INPUT_ENABLED = os.getenv('CAPTURE_STABLE_INPUT_ENABLED', '1') == '1'
CAPTURE_BUNDLE_ENABLED = os.getenv('CAPTURE_BUNDLE_ENABLED', '1') == '1'
CLOSE_BUNDLE_ENABLED = os.getenv('CLOSE_BUNDLE_ENABLED', '0') == '1'
ASSESSMENT_TRANSITION_ENABLED = os.getenv('ASSESSMENT_TRANSITION_ENABLED', '0') == '1'
ASSESSMENT_TRANSITION_USER_IDS = frozenset(filter(None, os.getenv('ASSESSMENT_TRANSITION_USER_IDS', '').split(',')))
ASSESSMENT_TRANSITION_COURSE_IDS = frozenset(filter(None, os.getenv('ASSESSMENT_TRANSITION_COURSE_IDS', '').split(',')))
ASSESSMENT_ADMISSION_ENABLED = os.getenv('ASSESSMENT_ADMISSION_ENABLED', '0') == '1'
ASSESSMENT_ADMISSION_TASK_IDS = frozenset(
    filter(None, os.getenv('ASSESSMENT_ADMISSION_TASK_IDS', '').split(','))
)
ASSESSMENT_ADMISSION_REDIS_URL = os.getenv(
    'ASSESSMENT_ADMISSION_REDIS_URL', 'redis://redis-admission:6379/0'
)
# Experimental configuration only; these defaults make no capacity claim.
ASSESSMENT_ADMISSION_GLOBAL_ENABLED = os.getenv('ASSESSMENT_ADMISSION_GLOBAL_ENABLED', '0') == '1'
ASSESSMENT_PREPARED_ADMISSION_MODE = os.getenv('ASSESSMENT_PREPARED_ADMISSION_MODE', 'redis')
SCHOOL_POOL_PROFILE = os.getenv('SCHOOL_POOL_PROFILE', 'six-pools-180-v1')
if ASSESSMENT_PREPARED_ADMISSION_MODE not in ('redis', 'postgres_transaction'):
    raise ImproperlyConfigured('Unknown prepared admission mode')
ASSESSMENT_ADMISSION_KEY_PREFIX = os.getenv('ASSESSMENT_ADMISSION_KEY_PREFIX', 'assessment:admission:global:v1')
ASSESSMENT_ADMISSION_CAPACITY = int(os.getenv('ASSESSMENT_ADMISSION_CAPACITY', '1'))
ASSESSMENT_ADMISSION_RATE_PER_SECOND = float(os.getenv('ASSESSMENT_ADMISSION_RATE_PER_SECOND', '1'))
ASSESSMENT_ADMISSION_BATCH_SIZE = int(os.getenv('ASSESSMENT_ADMISSION_BATCH_SIZE', '64'))
ASSESSMENT_ADMISSION_RECONCILIATION_GRACE_SECONDS = int(os.getenv('ASSESSMENT_ADMISSION_RECONCILIATION_GRACE_SECONDS', '60'))
ASSESSMENT_STARTUP_WORKERS = int(os.getenv('ASSESSMENT_STARTUP_WORKERS',
    '4' if ASSESSMENT_PREPARED_ADMISSION_MODE == 'postgres_transaction' else '2'))
ASSESSMENT_REMAINING_WORKERS = int(os.getenv('ASSESSMENT_REMAINING_WORKERS', '6'))
ASSESSMENT_STARTUP_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_STARTUP_DB_CONNECTIONS', str(ASSESSMENT_STARTUP_WORKERS)))
ASSESSMENT_REMAINING_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_REMAINING_DB_CONNECTIONS', '6'))
ASSESSMENT_AUTH_WORKERS = int(os.getenv('ASSESSMENT_AUTH_WORKERS', '4'))
ASSESSMENT_CAPTURE_WORKERS = int(os.getenv('ASSESSMENT_CAPTURE_WORKERS', '3'))
ASSESSMENT_STUDENT_READ_WORKERS = int(os.getenv('ASSESSMENT_STUDENT_READ_WORKERS', '1'))
ASSESSMENT_AUTH_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_AUTH_DB_CONNECTIONS', str(ASSESSMENT_AUTH_WORKERS)))
ASSESSMENT_CAPTURE_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_CAPTURE_DB_CONNECTIONS', str(ASSESSMENT_CAPTURE_WORKERS)))
ASSESSMENT_STUDENT_READ_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_STUDENT_READ_DB_CONNECTIONS', str(ASSESSMENT_STUDENT_READ_WORKERS)))
ASSESSMENT_ENTRY_WORKERS = int(os.getenv('ASSESSMENT_ENTRY_WORKERS', '0'))
ASSESSMENT_ENTRY_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_ENTRY_DB_CONNECTIONS', '0'))
ASSESSMENT_ENTRY_LOGIN_LIMIT = int(os.getenv('ASSESSMENT_ENTRY_LOGIN_LIMIT', '0'))
ASSESSMENT_ENTRY_STARTUP_LIMIT = int(os.getenv('ASSESSMENT_ENTRY_STARTUP_LIMIT', '0'))
ASSESSMENT_ENTRY_HEAVY_LIMIT = int(os.getenv('ASSESSMENT_ENTRY_HEAVY_LIMIT', '0'))
ASSESSMENT_ADMISSION_LEASE_SECONDS = int(
    os.getenv('ASSESSMENT_ADMISSION_LEASE_SECONDS', '180')
)
ASSESSMENT_ADMISSION_TTL_SECONDS = int(
    os.getenv('ASSESSMENT_ADMISSION_TTL_SECONDS', '900')
)
if SCHOOL_POOL_PROFILE == 'shared-entry-v1':
    _shared_http_pools = (ASSESSMENT_ENTRY_WORKERS, ASSESSMENT_REMAINING_WORKERS,
                         ASSESSMENT_CAPTURE_WORKERS, 1, 1)
    _shared_db_pools = (ASSESSMENT_ENTRY_DB_CONNECTIONS, ASSESSMENT_REMAINING_DB_CONNECTIONS,
                       ASSESSMENT_CAPTURE_DB_CONNECTIONS, 1, 1, 2, 1)
    if (ASSESSMENT_PREPARED_ADMISSION_MODE != 'postgres_transaction'
            or not ASSESSMENT_ADMISSION_ENABLED or not ASSESSMENT_ADMISSION_GLOBAL_ENABLED
            or min(_shared_http_pools) < 1 or sum(_shared_http_pools) > 24
            or min(_shared_db_pools) < 1 or sum(_shared_db_pools) > 23
            or min(ASSESSMENT_REMAINING_WORKERS, ASSESSMENT_REMAINING_DB_CONNECTIONS) < 4
            or min(ASSESSMENT_CAPTURE_WORKERS, ASSESSMENT_CAPTURE_DB_CONNECTIONS) < 3
            or not 1 <= ASSESSMENT_ENTRY_HEAVY_LIMIT < ASSESSMENT_ENTRY_WORKERS
            or not 1 <= ASSESSMENT_ENTRY_LOGIN_LIMIT <= ASSESSMENT_ENTRY_HEAVY_LIMIT
            or not 1 <= ASSESSMENT_ENTRY_STARTUP_LIMIT <= ASSESSMENT_ENTRY_HEAVY_LIMIT
            or not 1 <= ASSESSMENT_ADMISSION_CAPACITY <= min(ASSESSMENT_ENTRY_WORKERS,
                ASSESSMENT_ENTRY_DB_CONNECTIONS, ASSESSMENT_ENTRY_STARTUP_LIMIT)):
        raise ImproperlyConfigured('Shared entry requires independent bounded HTTP/DB budgets, protected writes and valid ingress/admission limits')
elif SCHOOL_POOL_PROFILE == 'shared-entry-v3':
    ASSESSMENT_CORE_WORKERS = int(os.getenv('ASSESSMENT_CORE_WORKERS', '1'))
    ASSESSMENT_CORE_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_CORE_DB_CONNECTIONS', '1'))
    ASSESSMENT_TEACHER_WORKERS = int(os.getenv('ASSESSMENT_TEACHER_WORKERS', '1'))
    ASSESSMENT_TEACHER_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_TEACHER_DB_CONNECTIONS', '1'))
    ASSESSMENT_SUBMISSION_WORKERS = int(os.getenv('ASSESSMENT_SUBMISSION_WORKERS', '1'))
    ASSESSMENT_SUBMISSION_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_SUBMISSION_DB_CONNECTIONS', '1'))
    ASSESSMENT_ANALYTICS_WORKERS = int(os.getenv('ASSESSMENT_ANALYTICS_WORKERS', '1'))
    ASSESSMENT_BACKGROUND_DB_CONNECTIONS = int(os.getenv('ASSESSMENT_BACKGROUND_DB_CONNECTIONS', '2'))
    ASSESSMENT_ENTRY_STARTUP_BURST = int(os.getenv('ASSESSMENT_ENTRY_STARTUP_BURST', '8'))
    ASSESSMENT_ENTRY_STARTUP_RATE = int(os.getenv('ASSESSMENT_ENTRY_STARTUP_RATE', '64'))
    PGBOUNCER_MAX_CLIENT_CONN = int(os.getenv('PGBOUNCER_MAX_CLIENT_CONN', '200'))
    SCHOOL_DB_MAX_CONNECTIONS = int(os.getenv('SCHOOL_DB_MAX_CONNECTIONS', '64'))
    SCHOOL_DB_RESERVED_CONNECTIONS = int(os.getenv('SCHOOL_DB_RESERVED_CONNECTIONS', '3'))
    SCHOOL_DB_DIRECT_CONNECTIONS = int(os.getenv('SCHOOL_DB_DIRECT_CONNECTIONS', '1'))
    SCHOOL_DB_MONITOR_CONNECTIONS = int(os.getenv('SCHOOL_DB_MONITOR_CONNECTIONS', '2'))
    SCHOOL_DB_MAINTENANCE_CONNECTIONS = int(os.getenv('SCHOOL_DB_MAINTENANCE_CONNECTIONS', '2'))
    SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS = int(os.getenv('SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS', '0'))
    ASSESSMENT_ENTRY_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_ENTRY_DB_RESERVE_CONNECTIONS', '0'))
    ASSESSMENT_REMAINING_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_REMAINING_DB_RESERVE_CONNECTIONS', '2'))
    ASSESSMENT_CAPTURE_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_CAPTURE_DB_RESERVE_CONNECTIONS', '1'))
    ASSESSMENT_CORE_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_CORE_DB_RESERVE_CONNECTIONS', '0'))
    ASSESSMENT_TEACHER_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_TEACHER_DB_RESERVE_CONNECTIONS', '0'))
    ASSESSMENT_BACKGROUND_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_BACKGROUND_DB_RESERVE_CONNECTIONS', '0'))
    ASSESSMENT_SUBMISSION_DB_RESERVE_CONNECTIONS = int(os.getenv('ASSESSMENT_SUBMISSION_DB_RESERVE_CONNECTIONS', '0'))
    _capacity_db_pools = (ASSESSMENT_ENTRY_DB_CONNECTIONS, ASSESSMENT_REMAINING_DB_CONNECTIONS,
        ASSESSMENT_CAPTURE_DB_CONNECTIONS, ASSESSMENT_CORE_DB_CONNECTIONS, ASSESSMENT_TEACHER_DB_CONNECTIONS,
        ASSESSMENT_BACKGROUND_DB_CONNECTIONS, ASSESSMENT_SUBMISSION_DB_CONNECTIONS)
    _capacity_reserves = (ASSESSMENT_ENTRY_DB_RESERVE_CONNECTIONS, ASSESSMENT_REMAINING_DB_RESERVE_CONNECTIONS,
        ASSESSMENT_CAPTURE_DB_RESERVE_CONNECTIONS, ASSESSMENT_CORE_DB_RESERVE_CONNECTIONS,
        ASSESSMENT_TEACHER_DB_RESERVE_CONNECTIONS, ASSESSMENT_BACKGROUND_DB_RESERVE_CONNECTIONS,
        ASSESSMENT_SUBMISSION_DB_RESERVE_CONNECTIONS)
    _capacity_extra_connections = (SCHOOL_DB_DIRECT_CONNECTIONS, SCHOOL_DB_MONITOR_CONNECTIONS,
        SCHOOL_DB_MAINTENANCE_CONNECTIONS, SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS)
    if (ASSESSMENT_PREPARED_ADMISSION_MODE != 'postgres_transaction'
            or not ASSESSMENT_ADMISSION_ENABLED or not ASSESSMENT_ADMISSION_GLOBAL_ENABLED
            or min(_capacity_db_pools) < 1
            or min(ASSESSMENT_ENTRY_WORKERS, ASSESSMENT_REMAINING_WORKERS, ASSESSMENT_CAPTURE_WORKERS,
                ASSESSMENT_CORE_WORKERS, ASSESSMENT_TEACHER_WORKERS, ASSESSMENT_SUBMISSION_WORKERS,
                ASSESSMENT_ANALYTICS_WORKERS, ASSESSMENT_ENTRY_STARTUP_BURST, ASSESSMENT_ENTRY_STARTUP_RATE,
                PGBOUNCER_MAX_CLIENT_CONN, SCHOOL_DB_MAX_CONNECTIONS) < 1
            or min(*_capacity_reserves, *_capacity_extra_connections, SCHOOL_DB_RESERVED_CONNECTIONS) < 0
            or not 1 <= ASSESSMENT_ENTRY_LOGIN_LIMIT <= ASSESSMENT_ENTRY_HEAVY_LIMIT < ASSESSMENT_ENTRY_WORKERS
            or not 1 <= ASSESSMENT_ENTRY_STARTUP_LIMIT <= ASSESSMENT_ENTRY_HEAVY_LIMIT
            or not 1 <= ASSESSMENT_ADMISSION_CAPACITY <= min(ASSESSMENT_ENTRY_WORKERS,
                ASSESSMENT_ENTRY_DB_CONNECTIONS, ASSESSMENT_ENTRY_STARTUP_LIMIT)
            or ASSESSMENT_BACKGROUND_DB_CONNECTIONS < 2 * ASSESSMENT_ANALYTICS_WORKERS
            or ASSESSMENT_SUBMISSION_DB_CONNECTIONS < ASSESSMENT_SUBMISSION_WORKERS
            or sum(_capacity_db_pools) + sum(_capacity_reserves) + sum(_capacity_extra_connections)
                > SCHOOL_DB_MAX_CONNECTIONS - SCHOOL_DB_RESERVED_CONNECTIONS):
        raise ImproperlyConfigured('Capacity v3 requires positive workers/pools, valid ingress/admission and a sufficient PostgreSQL connection budget')
elif SCHOOL_POOL_PROFILE == 'shared-entry-v2':
    ASSESSMENT_SUBMISSION_WORKERS = int(os.environ['ASSESSMENT_SUBMISSION_WORKERS'])
    ASSESSMENT_SUBMISSION_DB_CONNECTIONS = int(os.environ['ASSESSMENT_SUBMISSION_DB_CONNECTIONS'])
    ASSESSMENT_ANALYTICS_WORKERS = int(os.environ['ASSESSMENT_ANALYTICS_WORKERS'])
    ASSESSMENT_BACKGROUND_DB_CONNECTIONS = int(os.environ['ASSESSMENT_BACKGROUND_DB_CONNECTIONS'])
    ASSESSMENT_ENTRY_STARTUP_BURST = int(os.environ['ASSESSMENT_ENTRY_STARTUP_BURST'])
    ASSESSMENT_ENTRY_STARTUP_RATE = int(os.environ['ASSESSMENT_ENTRY_STARTUP_RATE'])
    SCHOOL_DB_MAX_CONNECTIONS = int(os.environ['SCHOOL_DB_MAX_CONNECTIONS'])
    SCHOOL_DB_RESERVED_CONNECTIONS = int(os.environ['SCHOOL_DB_RESERVED_CONNECTIONS'])
    SCHOOL_DB_DIRECT_CONNECTIONS = int(os.environ['SCHOOL_DB_DIRECT_CONNECTIONS'])
    SCHOOL_DB_MONITOR_CONNECTIONS = int(os.environ['SCHOOL_DB_MONITOR_CONNECTIONS'])
    SCHOOL_DB_MAINTENANCE_CONNECTIONS = int(os.environ['SCHOOL_DB_MAINTENANCE_CONNECTIONS'])
    SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS = int(os.environ['SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS'])
    _capacity_db_pools = (ASSESSMENT_ENTRY_DB_CONNECTIONS, ASSESSMENT_REMAINING_DB_CONNECTIONS,
        ASSESSMENT_CAPTURE_DB_CONNECTIONS, 1, 1, ASSESSMENT_BACKGROUND_DB_CONNECTIONS,
        ASSESSMENT_SUBMISSION_DB_CONNECTIONS)
    _capacity_extra_connections = (SCHOOL_DB_DIRECT_CONNECTIONS, SCHOOL_DB_MONITOR_CONNECTIONS,
        SCHOOL_DB_MAINTENANCE_CONNECTIONS, SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS)
    if (ASSESSMENT_PREPARED_ADMISSION_MODE != 'postgres_transaction'
            or not ASSESSMENT_ADMISSION_ENABLED or not ASSESSMENT_ADMISSION_GLOBAL_ENABLED
            or ASSESSMENT_ENTRY_WORKERS not in (15, 19)
            or not 1 <= ASSESSMENT_ENTRY_DB_CONNECTIONS <= 15
            or not 4 <= ASSESSMENT_REMAINING_WORKERS <= 12
            or not 4 <= ASSESSMENT_REMAINING_DB_CONNECTIONS <= 12
            or ASSESSMENT_CAPTURE_WORKERS != 3 or ASSESSMENT_CAPTURE_DB_CONNECTIONS != 3
            or ASSESSMENT_SUBMISSION_WORKERS not in (1, 2, 4)
            or ASSESSMENT_SUBMISSION_DB_CONNECTIONS != ASSESSMENT_SUBMISSION_WORKERS
            or ASSESSMENT_ANALYTICS_WORKERS not in (1, 2)
            or not 2 * ASSESSMENT_ANALYTICS_WORKERS <= ASSESSMENT_BACKGROUND_DB_CONNECTIONS <= 4
            or not 1 <= ASSESSMENT_ENTRY_HEAVY_LIMIT < ASSESSMENT_ENTRY_WORKERS
            or not 1 <= ASSESSMENT_ENTRY_LOGIN_LIMIT <= ASSESSMENT_ENTRY_HEAVY_LIMIT
            or not 1 <= ASSESSMENT_ENTRY_STARTUP_LIMIT <= ASSESSMENT_ENTRY_HEAVY_LIMIT
            or not 1 <= ASSESSMENT_ADMISSION_CAPACITY <= min(ASSESSMENT_ENTRY_WORKERS,
                ASSESSMENT_ENTRY_DB_CONNECTIONS, ASSESSMENT_ENTRY_STARTUP_LIMIT)
            or ASSESSMENT_ENTRY_STARTUP_BURST not in (8, 32)
            or ASSESSMENT_ENTRY_STARTUP_RATE != 64
            or SCHOOL_DB_MAX_CONNECTIONS != 64 or SCHOOL_DB_RESERVED_CONNECTIONS != 3
            or SCHOOL_DB_DIRECT_CONNECTIONS < 1 or SCHOOL_DB_MONITOR_CONNECTIONS < 2
            or SCHOOL_DB_MAINTENANCE_CONNECTIONS < 2 or SCHOOL_DB_ROLLING_OVERLAP_CONNECTIONS < 0
            or sum(_capacity_db_pools) + 3 + sum(_capacity_extra_connections)
                > SCHOOL_DB_MAX_CONNECTIONS - SCHOOL_DB_RESERVED_CONNECTIONS):
        raise ImproperlyConfigured('Capacity experiment exceeds approved worker, ingress or PostgreSQL connection budgets')
elif ASSESSMENT_PREPARED_ADMISSION_MODE == 'postgres_transaction':
    # Teacher/core each keep one HTTP connection; background/submission keep 2/1.
    _prepared_http_pools = (ASSESSMENT_AUTH_WORKERS, ASSESSMENT_STARTUP_WORKERS,
        ASSESSMENT_REMAINING_WORKERS, ASSESSMENT_CAPTURE_WORKERS, ASSESSMENT_STUDENT_READ_WORKERS, 1, 1)
    _prepared_db_pools = (ASSESSMENT_AUTH_DB_CONNECTIONS, ASSESSMENT_STARTUP_DB_CONNECTIONS,
        ASSESSMENT_REMAINING_DB_CONNECTIONS, ASSESSMENT_CAPTURE_DB_CONNECTIONS,
        ASSESSMENT_STUDENT_READ_DB_CONNECTIONS, 1, 1)
    if (min(_prepared_http_pools) < 1 or sum(_prepared_http_pools) != 20
            or _prepared_db_pools != _prepared_http_pools or sum(_prepared_db_pools) + 2 + 1 != 23
            or ASSESSMENT_ADMISSION_CAPACITY != ASSESSMENT_STARTUP_WORKERS):
        raise ImproperlyConfigured('Prepared transaction admission requires 20 HTTP workers, 23 ordinary DB connections and C equal to startup capacity')
elif (not 1 <= ASSESSMENT_STARTUP_WORKERS <= 7
        or ASSESSMENT_STARTUP_WORKERS + ASSESSMENT_REMAINING_WORKERS != 8
        or not 1 <= ASSESSMENT_STARTUP_DB_CONNECTIONS <= 7
        or ASSESSMENT_STARTUP_DB_CONNECTIONS + ASSESSMENT_REMAINING_DB_CONNECTIONS != 8):
    raise ImproperlyConfigured('Assessment startup allocation must split the existing eight workers/connections and reserve writes')
if ASSESSMENT_ADMISSION_GLOBAL_ENABLED and (
        not ASSESSMENT_ADMISSION_ENABLED
        or not 1 <= ASSESSMENT_ADMISSION_CAPACITY <= (min(ASSESSMENT_ENTRY_WORKERS, ASSESSMENT_ENTRY_DB_CONNECTIONS,
            ASSESSMENT_ENTRY_STARTUP_LIMIT) if SCHOOL_POOL_PROFILE in ('shared-entry-v1', 'shared-entry-v2', 'shared-entry-v3')
            else min(ASSESSMENT_STARTUP_WORKERS, ASSESSMENT_STARTUP_DB_CONNECTIONS))
        or not 0 < ASSESSMENT_ADMISSION_RATE_PER_SECOND < float('inf')
        or not 1 <= ASSESSMENT_ADMISSION_BATCH_SIZE <= 256
        or ASSESSMENT_ADMISSION_RECONCILIATION_GRACE_SECONDS < 0
        or ASSESSMENT_ADMISSION_LEASE_SECONDS <= 0
        or ASSESSMENT_ADMISSION_TTL_SECONDS < ASSESSMENT_ADMISSION_LEASE_SECONDS):
    raise ImproperlyConfigured('Experimental global admission requires explicit enablement, positive bounded budgets/rate/leases and C within startup budgets')
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
    "apps.api.middleware.RequestIdMiddleware",
    "apps.accounts.session_middleware.SessionMiddleware",
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
# Deprecated configuration compatibility only. Public entry and publication
# always use whole-paper assessment, including when old config sets this false.
ASSESSMENT_PREPARED_ENABLED = os.getenv('ASSESSMENT_PREPARED_ENABLED', '1') == '1'
ASSESSMENT_PAPER_ROOT = MEDIA_ROOT / 'assessment-papers'
ASSESSMENT_ACTIVATION_STATEMENT_TIMEOUT_MS = int(os.getenv('ASSESSMENT_ACTIVATION_STATEMENT_TIMEOUT_MS', '2000'))
ASSESSMENT_ACTIVATION_LOCK_TIMEOUT_MS = int(os.getenv('ASSESSMENT_ACTIVATION_LOCK_TIMEOUT_MS', '500'))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
STUDENT_COURSE_NAVIGATION_ENABLED = os.getenv('STUDENT_COURSE_NAVIGATION_ENABLED', '0') == '1'
STUDENT_COURSE_NAVIGATION_USER_IDS = frozenset(filter(None, os.getenv('STUDENT_COURSE_NAVIGATION_USER_IDS', '').split(',')))
STUDENT_COURSE_NAVIGATION_COURSE_IDS = frozenset(filter(None, os.getenv('STUDENT_COURSE_NAVIGATION_COURSE_IDS', '').split(',')))
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
