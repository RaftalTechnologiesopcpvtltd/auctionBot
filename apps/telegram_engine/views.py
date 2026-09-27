"""Webhook ingestion views for multi-tenant Telegram Bot updates."""
import json
import logging
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from apps.tenants.models import Tenant
from apps.telegram_engine.models import TelegramBotConfig, TelegramUpdateLog, BotType
from apps.telegram_engine.dispatcher import TelegramDispatcher

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class TelegramWebhookView(View):
    """Secure multi-tenant Telegram webhook ingestion endpoint."""

    http_method_names = ["post"]

    def post(
        self,
        request: HttpRequest,
        tenant_slug: str,
        bot_type: str = "UNIFIED",
        *args,
        **kwargs,
    ) -> HttpResponse:
        # 1. Parse JSON payload safely
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Invalid JSON payload received on webhook for tenant '%s': %s", tenant_slug, exc)
            return JsonResponse({"error": "Malformed JSON payload"}, status=400)

        # 2. Resolve Tenant
        try:
            tenant = Tenant.objects.get(slug=tenant_slug, is_active=True)
        except Tenant.DoesNotExist:
            logger.warning("Webhook received for unknown or inactive tenant slug: '%s'", tenant_slug)
            return JsonResponse({"error": "Tenant not found or inactive"}, status=404)

        # 3. Resolve TelegramBotConfig
        normalized_bot_type = bot_type.upper()
        try:
            bot_config = TelegramBotConfig.objects.get(
                tenant=tenant,
                bot_type=normalized_bot_type,
                is_active=True,
            )
        except TelegramBotConfig.DoesNotExist:
            logger.warning(
                "Webhook received for unconfigured or inactive bot_type '%s' on tenant '%s'",
                normalized_bot_type,
                tenant.slug,
            )
            return JsonResponse({"error": "Bot configuration not found or inactive"}, status=404)

        # 4. Webhook Security: Secret Token Validation
        if bot_config.webhook_secret_token:
            secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not secret_header or secret_header != bot_config.webhook_secret_token:
                logger.warning(
                    "Forbidden: Invalid webhook secret token on tenant '%s' (bot: %s)",
                    tenant.slug,
                    normalized_bot_type,
                )
                return JsonResponse({"error": "Forbidden: invalid secret token"}, status=403)

        # 5. Validate Update Structure
        update_id = payload.get("update_id")
        if update_id is None or not isinstance(update_id, int):
            logger.warning("Webhook payload missing valid 'update_id' for tenant '%s'", tenant.slug)
            return JsonResponse({"error": "Missing or invalid update_id"}, status=400)

        # 6. Idempotency Check & Logging
        if TelegramUpdateLog.objects.filter(tenant=tenant, update_id=update_id).exists():
            logger.info("Duplicate Telegram update '%s' received for tenant '%s'; acknowledged without re-processing.", update_id, tenant.slug)
            return JsonResponse({"status": "duplicate", "update_id": update_id}, status=200)

        # Record update receipt
        TelegramUpdateLog.objects.create(
            tenant=tenant,
            update_id=update_id,
            bot_type=normalized_bot_type,
        )

        # 7. Dispatch Update
        dispatcher = TelegramDispatcher(tenant=tenant, bot_config=bot_config)
        try:
            dispatch_result = dispatcher.dispatch(payload)
        except Exception as exc:
            logger.error(
                "Error dispatching Telegram update '%s' for tenant '%s': %s",
                update_id,
                tenant.slug,
                exc,
                exc_info=True,
            )
            # Return 200 to Telegram so it doesn't repeatedly retry a poisoned update, but log error internally
            return JsonResponse({"status": "error_handled", "update_id": update_id}, status=200)

        return JsonResponse({"status": "ok", "update_id": update_id, "result": dispatch_result}, status=200)

    def http_method_not_allowed(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        return JsonResponse({"error": "Method not allowed. Only POST is accepted."}, status=405)
