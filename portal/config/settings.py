"""Django settings for SSMS portal."""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load base .env first, then override with .env.test / .env.production.
# Set APP_ENV=test|production in the shell or in portal/.env.
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")
APP_ENV = os.getenv("APP_ENV", "test").strip().lower()
env_specific = BASE_DIR / f".env.{APP_ENV}"
if env_specific.exists():
    load_dotenv(env_specific, override=True)

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-dev-only-change-me-ssms-phase1",
)
DEBUG = os.getenv("DEBUG", "True").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "crispy_forms",
    "crispy_bootstrap5",
    "accounts",
    "catalog",
    "inventory",
    "sales",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.SystemAdminCustomersOnlyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.roles",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Harare"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
if not DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
}

LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "20"))
BRANCH_OFFLINE_HOURS = int(os.getenv("BRANCH_OFFLINE_HOURS", "24"))

# POS SQL Server (pricing) — used for GRV / purchase tonnage reports.
POS_SQL_CONNECTION_STRING = os.getenv(
    "POS_SQL_CONNECTION_STRING",
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DEVELOPER03\\SQLEXPRESS2012;"
    "Database=pricing;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;",
)

from django.contrib.messages import constants as message_constants

MESSAGE_TAGS = {
    message_constants.ERROR: "danger",
}
