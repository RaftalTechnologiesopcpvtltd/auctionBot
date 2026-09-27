"""Multi-tenant Telegram update dispatcher and command routing engine."""
import logging
from typing import Any, Dict, Optional, Tuple
from apps.tenants.models import Tenant
from apps.telegram_engine.models import TelegramBotConfig, TelegramUser, BotType
from apps.telegram_engine.seller_workflow import SellerWorkflow
from apps.telegram_engine.buyer_workflow import BuyerWorkflow
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
        self.seller_workflow = SellerWorkflow(
            tenant=self.tenant,
            bot_config=self.bot_config,
            telegram_service=self.telegram_service,
        )
        self.buyer_workflow = BuyerWorkflow(
            tenant=self.tenant,
            bot_config=self.bot_config,
            telegram_service=self.telegram_service,
        )

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
        bot_type = getattr(self.bot_config, "bot_type", BotType.UNIFIED)

        # 1. Handle Photo Uploads in active creation workflow
        if "photo" in message_data and user and chat_id:
            caption = message_data.get("caption", "")
            return self.seller_workflow.handle_photo(user, chat_id, message_data["photo"], caption=caption)

        # 2. Handle Video Uploads
        if "video" in message_data and user and chat_id:
            return self.seller_workflow.handle_video(user, chat_id, message_data["video"])

        text = message_data.get("text", "")
        command, args = self._parse_command(text)

        # 3. Route Commands
        if command:
            if command == "start" and user and chat_id:
                if bot_type == BotType.BUYER:
                    return self.buyer_workflow.handle_start(user, chat_id)
                elif bot_type == BotType.SELLER:
                    return self.seller_workflow.handle_start(user, chat_id)
                else:
                    return self.handle_start(user, chat_id, args)

            elif command == "cancel" and user and chat_id:
                if bot_type == BotType.BUYER:
                    return self.buyer_workflow.handle_cancel(user, chat_id)
                elif bot_type == BotType.SELLER:
                    return self.seller_workflow.handle_cancel(user, chat_id)

            elif command == "help" and user and chat_id:
                return self.handle_help(user, chat_id, args)

        # 4. Route Interactive / Menu Text Messages
        if user and chat_id and text:
            if bot_type == BotType.BUYER:
                return self.buyer_workflow.handle_text(user, chat_id, text)
            elif bot_type == BotType.SELLER:
                return self.seller_workflow.handle_text(user, chat_id, text)

        return self.handle_text_message(user, chat_id, text)

    def handle_start(
        self,
        user: Optional[TelegramUser],
        chat_id: int,
        args: str,
    ) -> Dict[str, Any]:
        """Unified fallback /start command."""
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
        """Handle the /help command customized for bot type."""
        helpdesk_details = (
            getattr(self.tenant, "metadata", {}).get("helpdesk_details")
            or f"Contact {self.tenant.name} support for inquiries."
        )
        try:
            self.telegram_service.send_message(chat_id=chat_id, text=str(helpdesk_details))
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
        chat_id = callback_data.get("message", {}).get("chat", {}).get("id") or (user.chat_id if user else None)
        bot_type = getattr(self.bot_config, "bot_type", BotType.UNIFIED)

        # Always acknowledge callback query to dismiss loading state on client if not already handled
        if callback_id and not (data_payload.startswith("wishlist_") or data_payload.startswith("remove_from_wishlist_") or data_payload.startswith("delete_")):
            try:
                self.telegram_service.answer_callback_query(
                    callback_query_id=callback_id,
                    text="Processing request...",
                )
            except Exception as exc:
                logger.warning("Could not answer callback query %s: %s", callback_id, exc)

        # Route through appropriate workflow
        if user and chat_id:
            if bot_type == BotType.BUYER:
                buyer_res = self.buyer_workflow.handle_callback(user, chat_id, callback_id, data_payload)
                if buyer_res.get("handled"):
                    return buyer_res
            elif bot_type == BotType.SELLER:
                seller_res = self.seller_workflow.handle_callback(user, chat_id, callback_id, data_payload)
                if seller_res.get("handled"):
                    return seller_res

        return {
            "handled": True,
            "type": "callback_query",
            "user_id": user.telegram_user_id if user else None,
            "callback_id": callback_id,
            "data": data_payload,
        }
