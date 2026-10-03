"""Production settings for AuctionBot.

Configured with security hardening, strict environment variable enforcement,
and production-ready logging.
"""
import os
from .base import *  # noqa: F403

DEBUG = False

# Enforce a non-default secret key in production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("DJANGO_SECRET_KEY must be provided in production.")

# Enforce explicit allowed hosts
allowed_hosts_raw = os.environ.get("DJANGO_ALLOWED_HOSTS")
if not allowed_hosts_raw:
    raise ValueError("DJANGO_ALLOWED_HOSTS must be explicitly defined in production.")
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts_raw.split(",") if host.strip()]
for default_host in [".auctionbot.shop", "auctionbot.shop", "72.62.248.151", "localhost", "127.0.0.1", "web"]:
    if default_host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(default_host)

# Security Hardening
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = os.environ.get("DJANGO_SESSION_COOKIE_SECURE", "True").lower() in ("true", "1", "yes")
CSRF_COOKIE_SECURE = os.environ.get("DJANGO_CSRF_COOKIE_SECURE", "True").lower() in ("true", "1", "yes")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

csrf_trusted_raw = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
if csrf_trusted_raw:
    CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in csrf_trusted_raw.split(",") if origin.strip()]
else:
    CSRF_TRUSTED_ORIGINS = []
for default_origin in [
    "http://*.auctionbot.shop",
    "https://*.auctionbot.shop",
    "http://auctionbot.shop",
    "https://auctionbot.shop",
    "http://72.62.248.151",
    "https://72.62.248.151",
    "http://localhost",
    "http://127.0.0.1",
]:
    if default_origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(default_origin)


# Production Logging (Console stream for containerized / 12-factor log management)
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "INFO"  # noqa: F405
LOGGING["loggers"]["apps.core"]["level"] = "INFO"  # noqa: F405
