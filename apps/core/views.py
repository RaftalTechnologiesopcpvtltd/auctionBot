"""Core health check and monitoring views."""
import logging
from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views import View
import redis

logger = logging.getLogger(__name__)


class HealthCheckView(View):
    """Comprehensive health check verifying application, database, and Redis."""

    def get(self, request, *args, **kwargs):
        status_code = 200
        response_data = {
            "status": "ok",
            "application": "ok",
            "database": "ok",
            "redis": "ok",
        }

        # 1. Verify Database Connectivity
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
        except Exception as exc:
            logger.error("Health check failed for database: %s", exc)
            response_data["database"] = f"unhealthy: {str(exc)}"
            response_data["status"] = "unhealthy"
            status_code = 503

        # 2. Verify Redis Connectivity
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            if not r.ping():
                raise ConnectionError("Redis ping did not return True")
        except Exception as exc:
            logger.error("Health check failed for Redis: %s", exc)
            response_data["redis"] = f"unhealthy: {str(exc)}"
            response_data["status"] = "unhealthy"
            status_code = 503

        return JsonResponse(response_data, status=status_code)


class LivenessCheckView(View):
    """Liveness probe: verifies that the web server process is running and accepting requests."""

    def get(self, request, *args, **kwargs):
        return JsonResponse({"status": "alive"}, status=200)


class ReadinessCheckView(View):
    """Readiness probe: verifies that the server is ready to accept traffic by testing backing services."""

    def get(self, request, *args, **kwargs):
        is_ready = True
        details = {}

        # Check DB
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1;")
                cursor.fetchone()
            details["database"] = "ready"
        except Exception as exc:
            details["database"] = f"not ready: {str(exc)}"
            is_ready = False

        # Check Redis
        try:
            r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            if r.ping():
                details["redis"] = "ready"
            else:
                details["redis"] = "not ready"
                is_ready = False
        except Exception as exc:
            details["redis"] = f"not ready: {str(exc)}"
            is_ready = False

        status_code = 200 if is_ready else 503
        return JsonResponse(
            {
                "status": "ready" if is_ready else "not ready",
                "details": details,
            },
            status=status_code,
        )
