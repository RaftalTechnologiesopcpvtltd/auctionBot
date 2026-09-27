"""Celery tasks for the Core application."""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="core.health_check_task")
def health_check_task():
    """Trivial Celery task used to verify task queue pipeline.

    Validates Django -> Celery -> Redis -> Worker end-to-end execution.
    """
    logger.info("core.health_check_task executed successfully.")
    return {
        "status": "ok",
        "task": "core.health_check_task",
        "message": "Celery worker pipeline is operational.",
    }
