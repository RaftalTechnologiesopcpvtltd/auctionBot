"""Production settings for AuctionBot.

Configured with security hardening, strict environment variable enforcement,
and production-ready logging.
"""
import os
from .base import *  # noqa: F403

DEBUG = False

# Enforce a non-default secret key in production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY or "dev-key" in SECRET_KEY or "insecure" in SECRET_KEY:
    raise ValueError("A secure, random DJANGO_SECRET_KEY must be provided in production.")

# Enforce explicit allowed hosts
allowed_hosts_raw = os.environ.get("DJANGO_ALLOWED_HOSTS")
if not allowed_hosts_raw:
    raise ValueError("DJANGO_ALLOWED_HOSTS must be explicitly defined in production.")
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_raw.split(",") if host.strip()]

# Security Hardening
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Production Logging (Console stream for containerized / 12-factor log management)
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "INFO"  # noqa: F405
LOGGING["loggers"]["apps.core"]["level"] = "INFO"  # noqa: F405
