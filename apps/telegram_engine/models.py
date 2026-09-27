"""Models for multi-tenant Telegram Bot configuration, user identity, and webhook idempotency."""
import os
from django.db import models
from apps.tenants.models import TenantOwnedModel
from apps.telegram_engine.security import encrypt_token, decrypt_token, mask_token


class BotType(models.TextChoices):
    UNIFIED = "UNIFIED", "Unified Bot"
    BUYER = "BUYER", "Buyer / Bidding Bot"
    SELLER = "SELLER", "Seller / Registration Bot"


class TelegramBotConfig(TenantOwnedModel):
    """Configuration for a tenant's Telegram bot instance."""

    bot_type = models.CharField(
        max_length=32,
        choices=BotType.choices,
        default=BotType.UNIFIED,
        help_text="Role or category of the Telegram bot within this tenant.",
    )
    bot_username = models.CharField(
        max_length=64,
        blank=True,
        help_text="Telegram bot handle without @ (e.g. CygAuctionBot).",
    )
    token_encrypted = models.TextField(
        blank=True,
        help_text="Encrypted Telegram bot API token (AES-128-CBC + HMAC-SHA256).",
    )
    token_env_var = models.CharField(
        max_length=128,
        blank=True,
        help_text="Optional environment variable name storing the bot token.",
    )
    webhook_secret_token = models.CharField(
        max_length=256,
        blank=True,
        help_text="Secret token for verifying X-Telegram-Bot-Api-Secret-Token webhook header.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this bot configuration is active and accepting webhook updates.",
    )

    class Meta:
        verbose_name = "Telegram Bot Configuration"
        verbose_name_plural = "Telegram Bot Configurations"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "bot_type"],
                name="unique_tenant_bot_type",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "is_active", "bot_type"]),
        ]

    def set_token(self, plain_token: str) -> None:
        """Encrypt and set token at rest."""
        self.token_encrypted = encrypt_token(plain_token)

    def get_token(self) -> str:
        """Resolve token from environment variable override or decrypt from storage."""
        if self.token_env_var:
            env_val = os.environ.get(self.token_env_var)
            if env_val:
                return env_val.strip()
        if self.token_encrypted:
            return decrypt_token(self.token_encrypted)
        return ""

    @property
    def masked_token(self) -> str:
        """Safe masked display of the token."""
        return mask_token(self.get_token())

    @property
    def webhook_path(self) -> str:
        """Returns the webhook path for this bot."""
        return f"/telegram/webhook/{self.tenant.slug}/{self.bot_type}/"

    @classmethod
    def ensure_dual_bots(cls, tenant):
        """Ensures that both SELLER and BUYER (Bidding) bot configurations exist for the given tenant."""
        seller_bot, _ = cls.objects.get_or_create(
            tenant=tenant,
            bot_type=BotType.SELLER,
            defaults={"is_active": True}
        )
        bidding_bot, _ = cls.objects.get_or_create(
            tenant=tenant,
            bot_type=BotType.BUYER,
            defaults={"is_active": True}
        )
        return seller_bot, bidding_bot

    def __str__(self) -> str:
        return f"{self.tenant.code} - {self.get_bot_type_display()} (@{self.bot_username or 'unset'})"


class TelegramUser(TenantOwnedModel):
    """Tenant-scoped Telegram user profile."""

    telegram_user_id = models.BigIntegerField(
        db_index=True,
        help_text="Stable numeric Telegram user ID from from_user.id.",
    )
    chat_id = models.BigIntegerField(
        db_index=True,
        help_text="Direct Telegram chat ID with this user.",
    )
    username = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Telegram @username if set by the user.",
    )
    first_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="First name from Telegram profile.",
    )
    last_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Last name from Telegram profile.",
    )
    language_code = models.CharField(
        max_length=16,
        blank=True,
        default="",
        help_text="IETF language tag reported by Telegram.",
    )
    is_blocked = models.BooleanField(
        default=False,
        help_text="Whether the user has blocked the bot or been banned.",
    )

    class Meta:
        verbose_name = "Telegram User"
        verbose_name_plural = "Telegram Users"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "telegram_user_id"],
                name="unique_tenant_telegram_user",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "telegram_user_id"]),
            models.Index(fields=["tenant", "chat_id"]),
        ]

    def __str__(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip() or self.username or str(self.telegram_user_id)
        return f"[{self.tenant.code}] {name} (TG: {self.telegram_user_id})"


class TelegramUpdateLog(TenantOwnedModel):
    """Tracks received update_id per tenant to guarantee webhook idempotency."""

    update_id = models.BigIntegerField(
        db_index=True,
        help_text="Telegram update_id delivered by webhook.",
    )
    bot_type = models.CharField(
        max_length=32,
        default="UNIFIED",
        help_text="Bot type through which this update was received.",
    )

    class Meta:
        verbose_name = "Telegram Update Log"
        verbose_name_plural = "Telegram Update Logs"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "update_id"],
                name="unique_tenant_telegram_update",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "update_id"]),
        ]

    def __str__(self) -> str:
        return f"[{self.tenant.code}] Update {self.update_id} ({self.created_at})"


class ConversationState(models.TextChoices):
    IDLE = "IDLE", "Idle"
    REGISTERING = "REGISTERING", "Registering as Seller"
    CREATING_LISTING = "CREATING_LISTING", "Creating Listing"
    REVIEWING_LISTING = "REVIEWING_LISTING", "Reviewing Listing"


class TelegramConversationState(TenantOwnedModel):
    """Tracks persistent multi-step interactive conversation states per user and bot."""

    telegram_user = models.ForeignKey(
        TelegramUser,
        on_delete=models.CASCADE,
        related_name="conversation_states",
        help_text="The Telegram user engaged in this conversation.",
    )
    bot_type = models.CharField(
        max_length=32,
        choices=BotType.choices,
        default=BotType.SELLER,
        help_text="Category of the Telegram bot context.",
    )
    state = models.CharField(
        max_length=64,
        choices=ConversationState.choices,
        default=ConversationState.IDLE,
        db_index=True,
        help_text="Current state in the workflow state machine.",
    )
    step = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Current sub-step or questionnaire field.",
    )
    context_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON payload storing in-progress form inputs, draft listing IDs, etc.",
    )

    class Meta:
        verbose_name = "Telegram Conversation State"
        verbose_name_plural = "Telegram Conversation States"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "telegram_user", "bot_type"],
                name="unique_tenant_user_bot_state",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "telegram_user", "bot_type"]),
            models.Index(fields=["tenant", "state"]),
        ]

    def reset(self) -> None:
        """Resets the conversation to idle and clears temporary context."""
        self.state = ConversationState.IDLE
        self.step = ""
        self.context_data = {}
        self.save(update_fields=["state", "step", "context_data", "updated_at"])

    def set_state(self, state: str, step: str = "", context_update: dict = None) -> None:
        """Transitions state and optionally updates context."""
        self.state = state
        self.step = step
        if context_update:
            if not isinstance(self.context_data, dict):
                self.context_data = {}
            self.context_data.update(context_update)
        self.save(update_fields=["state", "step", "context_data", "updated_at"])

    def __str__(self) -> str:
        return f"[{self.tenant.code}] {self.telegram_user} - {self.state} ({self.step})"

