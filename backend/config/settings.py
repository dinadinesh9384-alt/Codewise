"""Environment-driven Django settings for the CodeWise V2 backend."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean value.")


def _env_list(name):
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


DEBUG = _env_bool("DJANGO_DEBUG", default=False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if DEBUG:
        # Ephemeral development-only key; production must supply a secret via environment.
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")
elif not DEBUG and (len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 20
                    or SECRET_KEY.startswith("django-insecure-")):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be a high-entropy secret of at least 50 characters outside development mode."
    )

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    if DEBUG:
        ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
    else:
        raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set outside development mode.")
if not DEBUG and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS cannot contain a wildcard outside development mode.")

CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not DEBUG and any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Production CSRF trusted origins must use HTTPS.")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "api.apps.ApiConfig",
    "apps.repositories.apps.RepositoriesConfig",
    "agent_foundation",
    "llm_gateway",
    "orchestration",
    "rest_framework",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

AUTH_USER_MODEL = "repositories.User"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# SQLite is supported for local development and tests. Production requires PostgreSQL.
DATABASE_ENGINE = os.environ.get("DJANGO_DB_ENGINE", "django.db.backends.sqlite3").strip()
if DATABASE_ENGINE == "django.db.backends.sqlite3":
    DATABASE_NAME = os.environ.get("DJANGO_DB_NAME", str(BASE_DIR / "db.sqlite3"))
elif DATABASE_ENGINE == "django.db.backends.postgresql":
    DATABASE_NAME = os.environ.get("DJANGO_DB_NAME", "").strip()
    if not DATABASE_NAME:
        raise ImproperlyConfigured("DJANGO_DB_NAME is required for PostgreSQL.")
else:
    raise ImproperlyConfigured(
        "DJANGO_DB_ENGINE must be SQLite or PostgreSQL's Django backend."
    )
if not DEBUG and DATABASE_ENGINE != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Production requires PostgreSQL; SQLite is supported only for local development and tests.")

DATABASES = {
    "default": {
        "ENGINE": DATABASE_ENGINE,
        "NAME": DATABASE_NAME,
        "USER": os.environ.get("DJANGO_DB_USER", ""),
        "PASSWORD": os.environ.get("DJANGO_DB_PASSWORD", ""),
        "HOST": os.environ.get("DJANGO_DB_HOST", ""),
        "PORT": os.environ.get("DJANGO_DB_PORT", ""),
    }
}
if DATABASE_ENGINE == "django.db.backends.postgresql":
    if not DEBUG:
        sslmode = os.environ.get("DJANGO_DB_SSLMODE", "verify-full")
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise ImproperlyConfigured("Production PostgreSQL sslmode must require TLS verification.")
        DATABASES["default"]["OPTIONS"] = {"sslmode": sslmode}
        if os.environ.get("DJANGO_DB_SSLROOTCERT"):
            DATABASES["default"]["OPTIONS"]["sslrootcert"] = os.environ["DJANGO_DB_SSLROOTCERT"]
    try:
        DATABASES["default"]["CONN_MAX_AGE"] = int(os.environ.get("DJANGO_DB_CONN_MAX_AGE", "60"))
    except ValueError as exc:
        raise ImproperlyConfigured("DJANGO_DB_CONN_MAX_AGE must be an integer.") from exc
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
    if not DEBUG and not all(DATABASES["default"].get(key) for key in ("USER", "PASSWORD", "HOST")):
        raise ImproperlyConfigured("Production PostgreSQL requires DJANGO_DB_USER, DJANGO_DB_PASSWORD and DJANGO_DB_HOST.")

CODEWISE_API_WORKFLOW_FACTORY = os.environ.get(
    "CODEWISE_API_WORKFLOW_FACTORY", "config.workflow_runtime.build_api_workflow_runtime"
).strip() or None
CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED = _env_bool("CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED", default=False)

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Repository snapshots are private application data, never served as static or
# media assets. Deployments may place this directory on a private volume.
REPOSITORY_PRIVATE_STORAGE_ROOT = Path(
    os.environ.get("CODEWISE_PRIVATE_REPOSITORY_STORAGE", str(BASE_DIR / "private_repository_storage"))
).expanduser()
if not REPOSITORY_PRIVATE_STORAGE_ROOT.is_absolute():
    raise ImproperlyConfigured("CODEWISE_PRIVATE_REPOSITORY_STORAGE must be an absolute private path.")
if not DEBUG and "CODEWISE_PRIVATE_REPOSITORY_STORAGE" not in os.environ:
    raise ImproperlyConfigured("Configure CODEWISE_PRIVATE_REPOSITORY_STORAGE on a private persistent volume in production.")
if not DEBUG and REPOSITORY_PRIVATE_STORAGE_ROOT.is_relative_to(BASE_DIR):
    raise ImproperlyConfigured("Production repository storage must be outside the application directory.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
try:
    SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_COOKIE_AGE", "43200"))
except ValueError as exc:
    raise ImproperlyConfigured("DJANGO_SESSION_COOKIE_AGE must be an integer.") from exc
if SESSION_COOKIE_AGE < 300:
    raise ImproperlyConfigured("DJANGO_SESSION_COOKIE_AGE must be at least five minutes.")
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
if not DEBUG and not SECURE_SSL_REDIRECT:
    raise ImproperlyConfigured("DJANGO_SECURE_SSL_REDIRECT cannot be disabled outside development mode.")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
try:
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
except ValueError as exc:
    raise ImproperlyConfigured("DJANGO_SECURE_HSTS_SECONDS must be an integer.") from exc
if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured("DJANGO_SECURE_HSTS_SECONDS cannot be negative.")
if not DEBUG and SECURE_HSTS_SECONDS < 1:
    raise ImproperlyConfigured("Set DJANGO_SECURE_HSTS_SECONDS to a positive value outside development mode.")
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD")
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
if _env_bool("DJANGO_TRUST_X_FORWARDED_PROTO"):
    # Enable only behind a trusted proxy which overwrites this header and blocks direct app access.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CONN_MAX_AGE = int(os.environ.get("DJANGO_DB_CONN_MAX_AGE", "0"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.logging.JsonLogFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "codewise": {"handlers": ["console"], "level": os.environ.get("CODEWISE_LOG_LEVEL", "INFO"),
                     "propagate": False},
    },
}

REST_FRAMEWORK = {
    # Session authentication is CSRF-protected by Django middleware. Public health
    # explicitly opts out on its own view.
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
    "UNAUTHENTICATED_TOKEN": None,
}
