"""URL patterns for multi-tenant Telegram webhook endpoints."""
from django.urls import path
from apps.telegram_engine.views import TelegramWebhookView

app_name = "telegram_engine"

urlpatterns = [
    path(
        "webhook/<slug:tenant_slug>/",
        TelegramWebhookView.as_view(),
        name="webhook-default",
    ),
    path(
        "webhook/<slug:tenant_slug>/<str:bot_type>/",
        TelegramWebhookView.as_view(),
        name="webhook-typed",
    ),
]
