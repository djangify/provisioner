"""
Provisioner - eBuilder Managed Hosting Service
Django settings
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from decouple import config

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")

DEBUG = False

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origin.strip()
]


# Domain settings
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "ebuilder.host")
# Where the signup form lives (djangify.com marketing site)
DJANGIFY_DOMAIN = os.environ.get("DJANGIFY_DOMAIN", "djangify.com")
NGINX_CONFIG_DIR = os.environ.get("NGINX_CONFIG_DIR", "/etc/nginx/sites-enabled")
WILDCARD_SSL_CERT = os.environ.get(
    "WILDCARD_SSL_CERT", f"/etc/letsencrypt/live/{BASE_DOMAIN}/fullchain.pem"
)
WILDCARD_SSL_KEY = os.environ.get(
    "WILDCARD_SSL_KEY", f"/etc/letsencrypt/live/{BASE_DOMAIN}/privkey.pem"
)
SERVER_IP = config("SERVER_IP")

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    # Local
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "provisioner.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "provisioner.wsgi.application"

# Database - SQLite for simplicity
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db" / "provisioner.sqlite3",
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
}

# CORS settings - Allow djangify.com to call provisioner API
CORS_ALLOW_ALL_ORIGINS = False

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS", "https://djangify.com,https://www.djangify.com"
    ).split(",")
    if origin.strip()
]

# Allow credentials (cookies, authorization headers)
CORS_ALLOW_CREDENTIALS = True

# Allow these headers
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]

# Allow these methods
CORS_ALLOW_METHODS = [
    "GET",
    "POST",
    "OPTIONS",
]

# ====================================================================
# PROVISIONER SETTINGS
# ====================================================================

# Stripe
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")  # Your monthly price ID

# Docker
EBUILDER_IMAGE = os.environ.get("EBUILDER_IMAGE", "djangify/ebuilder-web:latest")
CUSTOMER_DATA_ROOT = os.environ.get("CUSTOMER_DATA_ROOT", "/srv/customers")
CONTAINER_NETWORK = os.environ.get("CONTAINER_NETWORK", "ebuilder-network")

# Port allocation (each customer gets a port)
PORT_RANGE_START = int(os.environ.get("PORT_RANGE_START", "8100"))
PORT_RANGE_END = int(os.environ.get("PORT_RANGE_END", "8999"))


# Email settings (for sending login details)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "djangify@djangify.com")


# Admin customization
ADMIN_SITE_HEADER = "eBuilder Provisioner"
ADMIN_SITE_TITLE = "eBuilder Managed Hosting"
ADMIN_INDEX_TITLE = "Instance Management"
