"""Automated test suite for Phase 01 Foundation.

Tests:
1. Application startup and settings loading
2. Database (PostgreSQL) connectivity
3. Redis connectivity
4. Celery task execution
5. Health endpoints (/health/, /health/live/, /health/ready/)
6. Configuration validation
"""
from unittest.mock import patch
import redis
from django.conf import settings
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from apps.core.tasks import health_check_task


class ApplicationStartupTest(TestCase):
    """Test that Django core configuration initializes properly."""

    def test_settings_loaded(self):
        """Verify essential settings are present and correctly typed."""
        self.assertIsNotNone(settings.SECRET_KEY)
        self.assertTrue(len(settings.SECRET_KEY) > 0)
        self.assertIn("apps.core.apps.CoreConfig", settings.INSTALLED_APPS)
        self.assertEqual(settings.ROOT_URLCONF, "config.urls")
        self.assertTrue(hasattr(settings, "STATIC_URL"))
        self.assertTrue(hasattr(settings, "MEDIA_URL"))


class DatabaseConnectionTest(TestCase):
    """Test PostgreSQL database connectivity through Django."""

    def test_database_connection_and_query(self):
        """Verify that connection to PostgreSQL can execute queries."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 AS probe;")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1)


class RedisConnectionTest(TestCase):
    """Test Redis connectivity."""

    def test_redis_ping_and_operations(self):
        """Verify Redis connection, ping, and basic key-value operations."""
        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        self.assertTrue(r.ping())

        # Test transient probe key
        test_key = "probe:test:phase01"
        r.set(test_key, "phase01_ok", ex=10)
        val = r.get(test_key)
        self.assertEqual(val.decode("utf-8"), "phase01_ok")
        r.delete(test_key)


class CeleryTaskExecutionTest(TestCase):
    """Test Celery task execution."""

    def test_health_check_task_direct_execution(self):
        """Verify health check task returns expected response structure."""
        result = health_check_task.run()
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("task"), "core.health_check_task")

    def test_health_check_task_apply_eager(self):
        """Verify task execution through Celery task pipeline."""
        task_async = health_check_task.apply()
        self.assertTrue(task_async.successful())
        self.assertEqual(task_async.result.get("status"), "ok")


class HealthEndpointsTest(TestCase):
    """Test health check endpoints: /health/, /health/live/, /health/ready/."""

    def test_health_endpoint_all_healthy(self):
        """GET /health/ returns 200 and healthy status for all systems."""
        response = self.client.get(reverse("health-check"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("application"), "ok")
        self.assertEqual(data.get("database"), "ok")
        self.assertEqual(data.get("redis"), "ok")

    def test_liveness_endpoint(self):
        """GET /health/live/ returns 200 and alive status."""
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "alive")

    def test_readiness_endpoint_ready(self):
        """GET /health/ready/ returns 200 when backing services are ready."""
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ready")
        self.assertEqual(data.get("details", {}).get("database"), "ready")
        self.assertEqual(data.get("details", {}).get("redis"), "ready")

    @patch("django.db.connection.cursor")
    def test_health_endpoint_db_failure(self, mock_cursor):
        """GET /health/ returns 503 if database fails."""
        mock_cursor.side_effect = Exception("Database connection failure simulation")
        response = self.client.get(reverse("health-check"))
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data.get("status"), "unhealthy")
        self.assertIn("unhealthy", data.get("database"))

    @patch("redis.Redis.from_url")
    def test_health_endpoint_redis_failure(self, mock_redis_from_url):
        """GET /health/ returns 503 if Redis fails."""
        mock_instance = mock_redis_from_url.return_value
        mock_instance.ping.side_effect = ConnectionError("Redis unreachable simulation")
        response = self.client.get(reverse("health-check"))
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data.get("status"), "unhealthy")
        self.assertIn("unhealthy", data.get("redis"))


class ConfigurationValidationTest(TestCase):
    """Test configuration validation rules."""

    def test_allowed_hosts_configured(self):
        """Verify allowed hosts has entries."""
        self.assertTrue(len(settings.ALLOWED_HOSTS) > 0)

    def test_database_engine_is_postgresql(self):
        """Verify primary database engine is PostgreSQL in standard configuration."""
        default_db = settings.DATABASES["default"]
        self.assertIn("postgresql", default_db["ENGINE"])
