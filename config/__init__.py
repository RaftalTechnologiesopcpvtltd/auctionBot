"""Config package initialization.

Ensures that the Celery app is always imported when Django starts so that
shared_task decorators work properly.
"""
from .celery import app as celery_app

__all__ = ("celery_app",)
