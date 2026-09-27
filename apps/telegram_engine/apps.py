"""Telegram Engine application configuration."""
from django.apps import AppConfig


class TelegramEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telegram_engine"
    verbose_name = "Telegram Engine"
