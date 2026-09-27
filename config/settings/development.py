"""Development settings for AuctionBot.

Used for local development and testing.
"""
from .base import *  # noqa: F403

DEBUG = True

# Allow local access in development
ALLOWED_HOSTS = ["*"]

# Optional SQLite override for isolated local unit tests if explicitly requested via env
if os.environ.get("USE_SQLITE_TEST", "False").lower() in ("true", "1"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test_db.sqlite3",  # noqa: F405
        }
    }
