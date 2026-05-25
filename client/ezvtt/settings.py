"""
Minimal Django settings for the EzVTT UI app (DEMO).

This app is bound to localhost only and sits BEHIND the Java edge gateway.
It never faces the internet, so it trusts the identity headers injected by Java
(X-EzVTT-Role / X-EzVTT-User). In production those headers will be HMAC-signed.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# DEMO key only — not for production.
SECRET_KEY = "demo-insecure-key-replace-before-production"
DEBUG = True

# Only ever reached via the Java proxy, which rewrites Host to 127.0.0.1.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = []
MIDDLEWARE = []

ROOT_URLCONF = "ezvtt.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {"context_processors": []},
    }
]

WSGI_APPLICATION = "ezvtt.wsgi.application"

# No database needed for the demo views.
DATABASES = {}

USE_TZ = True
