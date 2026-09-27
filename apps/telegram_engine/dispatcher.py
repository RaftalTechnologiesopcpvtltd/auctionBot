"""Multi-tenant Telegram update dispatcher and command routing engine."""
import logging
from typing import Any, Dict, Optional, Tuple
from apps.tenants.models import Tenant
from apps.telegram_engine.models import TelegramBotConfig, TelegramUser
from services.telegram import TelegramService

logger = logging.getLogger(__name__)


class TelegramDispatcher:
    """Dispatches incoming Telegram updates to appropriate command, message, or callback handlers."""

    def __init__(
        self,
        tenant: Tenant,
        bot_config: TelegramBotConfig,
        telegram_service: Optional[TelegramService] = None,
    ):
        self.tenant = tenant
        self.bot_config = bot_config
        self.telegram_service = telegram_service or TelegramService(bot_config)

    def dispatch(self, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Route an incoming update to the appropriate handler based on its payload structure."""
        if "message" in update_data:
            return self._handle_message_update(update_data["message"])
        elif "callback_query" in update_data:
            return self._handle_callback_update(update_data["callback_query"])
        else:
            logger.info("Ignored unsupported Telegram update type for tenant '%s'", self.tenant.slug)
            return {"handled": False, "reason": "unsupported_update_type"}

    def _sync_user(
        self,
        user_data: Optional[Dict[str, Any]],
        chat_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[TelegramUser]:
        """Upsert a tenant-scoped TelegramUser record from Telegram profile data."""
        if not user_data or "id" not in user_data:
            return None

        telegram_user_id = user_data["id"]
        chat_id = chat_data.get("id", telegram_user_id) if chat_data else telegram_user_id

        user, _ = TelegramUser.objects.update_or_create(
            tenant=self.tenant,
            telegram_user_id=telegram_user_id,
            defaults={
                "chat_id": chat_id,
                "username": user_data.get("username"),
                "first_name": user_data.get("first_name", "") or "",
                "last_name": user_data.get("last_name", "") or "",
                "language_code": user_data.get("language_code", "") or "",
            },
        )
        return user

    def _parse_command(self, text: str) -> Tuple[Optional[str], str]:
        """Extract command and arguments from a text string starting with '/'."""
        if not text or not text.startswith("/"):
            return None, ""
        parts = text.strip().split(maxsplit=1)
        command_raw = parts[0][1:]  # remove leading '/'
        # Handle bot username mentions, e.g. /start@CygBot
        command = command_raw.split("@")[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        return command, args

    def _handle_message_update(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        user = self._sync_user(message_data.get("from"), message_data.get("chat"))
        chat_id = message_data.get("chat", {}).get("id")
        text = message_data.get("text", "")

        command, args = self._parse_command(text)
        if command:
            return self._route_command(user, chat_id, command, args)

        return self.handle_text_message(user, chat_id, text)

    def _route_command(
        self,
        user: Optional[TelegramUser],
        chat_id: int,
        command: str,
        args: str,
    ) -> Dict[str, Any]:
        """Route recognized commands to their dedicated handler functions."""
        if command == "start":
            return self.handle_start(user, chat_id, args)
        elif command == "help":
            return self.handle_help(user, chat_id, args)
        else:
            return self.handle_unknown_command(user, chat_id, command)

    def handle_start(
        self,
        user: Optional[TelegramUser],
        chat_id: int,
        args: str,
    ) -> Dict[str, Any]:
        """Handle the /start command."""
        user_name = user.first_name if user and user.first_name else "Guest"
        text = (
            f"Hello {user_name}! 👋\n\n"
            f"Welcome to <b>{self.tenant.name}</b> auction bot.\n"
            f"Official currency: <b>{self.tenant.currency}</b> | Timezone: <b>{self.tenant.timezone}</b>\n\n"
            "Use /help to view available commands."
        )
        try:
            self.telegram_service.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Could not send /start message: %s", exc)

        return {
            "handled": True,
            "type": "command",
            "command": "start",
            "user_id": user.telegram_user_id if user else None,
            "chat_id": chat_id,
        }

    def handle_help(
        self,
        user: Optional[TelegramUser],
        chat_id: int,
        args: str,
    ) -> Dict[str, Any]:
        """Handle the /help command."""
        text = (
            f"<b>{self.tenant.name} Support & Commands</b>\n\n"
            "Available commands:\n"
            "• /start - Restart the bot and view tenant welcome\n"
            "• /help - View this help documentation\n\n"
            f"For inquiries, contact your regional {self.tenant.name} team."
        )
        try:
            self.telegram_service.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Could not send /help message: %s", exc)

        return {
            "handled": True,
            "type": "command",
            "command": "help",
            "user_id": user.telegram_user_id if user else None,
            "chat_id": chat_id,
        }

    def handle_unknown_command(
        self,
        user: Optional[TelegramUser],
        chat_id: int,
        command: str,
    ) -> Dict[str, Any]:
        """Handle unrecognized commands gracefully."""
        text = f"Unrecognized command: /{command}\nUse /help to see what you can do."
        try:
            self.telegram_service.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            logger.warning("Could not send unknown command message: %s", exc)

        return {
            "handled": True,
            "type": "command",
            "command": command,
            "status": "unrecognized",
            "chat_id": chat_id,
        }

    def handle_text_message(
        self,
        user: Optional[TelegramUser],
        chat_id: int,
        text: str,
    ) -> Dict[str, Any]:
        """Handle non-command text messages."""
        return {
            "handled": True,
            "type": "text_message",
            "user_id": user.telegram_user_id if user else None,
            "chat_id": chat_id,
            "length": len(text),
        }

    def _handle_callback_update(self, callback_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle callback queries triggered by inline keyboard buttons."""
        user = self._sync_user(callback_data.get("from"))
        callback_id = callback_data.get("id")
        data_payload = callback_data.get("data", "")

        # Always acknowledge callback query to dismiss loading state on client
        if callback_id:
            try:
                self.telegram_service.answer_callback_query(
                    callback_query_id=callback_id,
                    text="Processing request...",
                )
            except Exception as exc:
                logger.warning("Could not answer callback query %s: %s", callback_id, exc)

        return {
            "handled": True,
            "type": "callback_query",
            "user_id": user.telegram_user_id if user else None,
            "callback_id": callback_id,
            "data": data_payload,
        }
