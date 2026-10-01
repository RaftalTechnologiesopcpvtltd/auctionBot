"""Tenant-aware Telegram API service abstraction.

Provides controlled operations (send_message, edit_message_text, answer_callback_query)
bound explicitly to a Tenant's Telegram bot configuration.
"""
import logging
from typing import Any, Dict, Optional
import requests
from apps.tenants.models import Tenant
from apps.telegram_engine.models import TelegramBotConfig, BotType

logger = logging.getLogger(__name__)


class TelegramServiceError(Exception):
    """Base exception for Telegram service errors."""
    pass


class TelegramService:
    """Service abstraction for executing Telegram Bot API calls within a Tenant context."""

    BASE_URL = "https://api.telegram.org/bot"

    def __init__(
        self,
        tenant_or_config: Any,
        bot_type: str = BotType.UNIFIED,
        http_client: Optional[Any] = None,
    ):
        if isinstance(tenant_or_config, TelegramBotConfig):
            self.config = tenant_or_config
            self.tenant = tenant_or_config.tenant
        elif isinstance(tenant_or_config, Tenant):
            self.tenant = tenant_or_config
            try:
                self.config = TelegramBotConfig.objects.get(
                    tenant=self.tenant,
                    bot_type=bot_type,
                    is_active=True,
                )
            except TelegramBotConfig.DoesNotExist:
                raise TelegramServiceError(
                    f"No active TelegramBotConfig found for tenant '{self.tenant.slug}' and bot_type '{bot_type}'."
                )
        else:
            raise ValueError("TelegramService requires a Tenant or TelegramBotConfig instance.")

        self.http_client = http_client or requests.Session()

    def _get_api_url(self, method: str) -> str:
        token = self.config.get_token()
        if not token:
            raise TelegramServiceError(
                f"Cannot execute Telegram API call '{method}': No token configured for tenant '{self.tenant.slug}'."
            )
        return f"{self.BASE_URL}{token}/{method}"

    def _execute_api_call(
        self,
        method: str,
        payload: Dict[str, Any],
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self._get_api_url(method)
        try:
            if files:
                import json
                data_payload = {}
                for k, v in payload.items():
                    if isinstance(v, (dict, list)):
                        data_payload[k] = json.dumps(v)
                    elif v is not None:
                        data_payload[k] = str(v)
                response = self.http_client.post(url, data=data_payload, files=files, timeout=30)
            else:
                response = self.http_client.post(url, json=payload, timeout=10)
            data = response.json()
            if not data.get("ok"):
                logger.error(
                    "Telegram API '%s' returned error for tenant '%s': %s",
                    method,
                    self.tenant.slug,
                    data.get("description"),
                )
            return data
        except Exception as exc:
            logger.error(
                "Telegram API '%s' call failed for tenant '%s': %s",
                method,
                self.tenant.slug,
                exc,
            )
            raise TelegramServiceError(f"Telegram API request failed: {exc}") from exc

    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a text message to a specific Telegram chat."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._execute_api_call("sendMessage", payload)

    def send_photo(
        self,
        chat_id: int,
        photo: Any,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a photo via file_id/URL or local file object."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "parse_mode": parse_mode,
        }
        if caption:
            payload["caption"] = caption
        if reply_markup:
            payload["reply_markup"] = reply_markup

        if isinstance(photo, str):
            payload["photo"] = photo
            return self._execute_api_call("sendPhoto", payload)
        else:
            files = {"photo": photo}
            return self._execute_api_call("sendPhoto", payload, files=files)

    def send_video(
        self,
        chat_id: int,
        video: Any,
        caption: Optional[str] = None,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a video via file_id/URL or local file object."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "parse_mode": parse_mode,
        }
        if caption:
            payload["caption"] = caption
        if reply_markup:
            payload["reply_markup"] = reply_markup

        if isinstance(video, str):
            payload["video"] = video
            return self._execute_api_call("sendVideo", payload)
        else:
            files = {"video": video}
            return self._execute_api_call("sendVideo", payload, files=files)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Edit the text of an existing Telegram message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._execute_api_call("editMessageText", payload)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> Dict[str, Any]:
        """Acknowledge an incoming callback query from an inline keyboard button."""
        payload: Dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        return self._execute_api_call("answerCallbackQuery", payload)

    def get_me(self) -> Dict[str, Any]:
        """Fetch bot identity details from Telegram Bot API to verify token connectivity."""
        return self._execute_api_call("getMe", {})

    def set_webhook(
        self,
        url: str,
        secret_token: Optional[str] = None,
        allowed_updates: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Register a public webhook endpoint URL with Telegram Bot API."""
        payload: Dict[str, Any] = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        return self._execute_api_call("setWebhook", payload)

    def get_webhook_info(self) -> Dict[str, Any]:
        """Fetch current webhook delivery configuration and status from Telegram."""
        return self._execute_api_call("getWebhookInfo", {})

    def delete_webhook(self, drop_pending_updates: bool = False) -> Dict[str, Any]:
        """Remove webhook registration from Telegram."""
        return self._execute_api_call("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def get_file(self, file_id: str) -> Dict[str, Any]:
        """Fetch metadata for a file including its file_path from Telegram."""
        return self._execute_api_call("getFile", {"file_id": file_id})

    def download_file(self, file_path: str, destination_path: str) -> str:
        """Download a Telegram file to local storage securely."""
        token = self.config.get_token()
        if not token:
            raise TelegramServiceError("No token configured to download file.")
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        response = self.http_client.get(url, stream=True, timeout=30)
        response.raise_for_status()
        import os
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with open(destination_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return destination_path

