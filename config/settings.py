"""
Django settings for config project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "core.apps.CoreConfig",
    "invoicing.apps.InvoicingConfig",
    "payroll.apps.PayrollConfig",
    "imports.apps.ImportsConfig",
    "notifications.apps.NotificationsConfig",
    "payments.apps.PaymentsConfig",
    "reports.apps.ReportsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
                "accounts.context_processors.user_role",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("USE_SQLITE", "true").lower() == "true":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("MYSQL_DB", "automated_invoicing_payroll"),
            "USER": os.getenv("MYSQL_USER", "root"),
            "PASSWORD": os.getenv("MYSQL_PASSWORD", ""),
            "HOST": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "PORT": os.getenv("MYSQL_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Singapore"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
AUTHENTICATION_BACKENDS = [
    "accounts.auth_backends.EmailOrUsernameBackend",
    "django.contrib.auth.backends.ModelBackend",
]

COMPANY_NAME = os.getenv("COMPANY_NAME", "Automated Invoicing and Payroll Pte Ltd")
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "finance@example.com")
COMPANY_PHONE = os.getenv("COMPANY_PHONE", "+65 6000 0000")
COMPANY_ADDRESS = os.getenv("COMPANY_ADDRESS", "123 Business Street, Singapore 123456")
COMPANY_REG_NO = os.getenv("COMPANY_REG_NO", "201535968M")
REGISTERED_OFFICE_TEXT = os.getenv(
    "REGISTERED_OFFICE_TEXT",
    "Attention: finance@vaniday.com, 7 Temasek Boulevard, #12-07 Suntec Tower One, Singapore 038987, Singapore.",
)
INVOICE_PAYMENT_TERM_DAYS = int(os.getenv("INVOICE_PAYMENT_TERM_DAYS", "30"))
INVOICE_BANK_TEXT = os.getenv(
    "INVOICE_BANK_TEXT",
    "Vaniday Singapore Pte Ltd\n"
    "Bank: Oversea-Chinese Banking Corporation Limited (OCBC)\n"
    "BIC/SWIFT: OCBCSGSGXXX\n"
    "Account Number: 695105486001\n"
    "Payment via PayNow to 201535968M",
)
INVOICE_PAYMENT_NOTES = os.getenv(
    "INVOICE_PAYMENT_NOTES",
    "Please include your invoice number and salon name as reference for electronic payments.\n"
    "We will payout within 10 days from Invoice Date.\n"
    "This is a computer generated invoice and therefore no signature is required.",
)

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", COMPANY_EMAIL)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "20"))

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
