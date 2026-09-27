"""Django Admin registration for Telegram Engine models."""
from django.contrib import admin
from apps.telegram_engine.models import TelegramBotConfig, TelegramUser, TelegramUpdateLog


@admin.register(TelegramBotConfig)
class TelegramBotConfigAdmin(admin.ModelAdmin):
    list_display = ("tenant", "bot_type", "bot_username", "masked_token", "is_active", "created_at")
    list_filter = ("bot_type", "is_active", "tenant")
    search_fields = ("bot_username", "tenant__name", "tenant__code")
    readonly_fields = ("masked_token", "created_at", "updated_at")
    exclude = ("token_encrypted",)  # Never expose encrypted ciphertext directly in admin forms

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ("tenant", "telegram_user_id", "username", "first_name", "last_name", "is_blocked", "created_at")
    list_filter = ("tenant", "is_blocked")
    search_fields = ("telegram_user_id", "username", "first_name", "last_name", "tenant__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(TelegramUpdateLog)
class TelegramUpdateLogAdmin(admin.ModelAdmin):
    list_display = ("tenant", "update_id", "bot_type", "created_at")
    list_filter = ("tenant", "bot_type")
    search_fields = ("update_id", "tenant__name")
    readonly_fields = ("tenant", "update_id", "bot_type", "created_at", "updated_at")


from apps.telegram_engine.models import TelegramConversationState


@admin.register(TelegramConversationState)
class TelegramConversationStateAdmin(admin.ModelAdmin):
    list_display = ("tenant", "telegram_user", "bot_type", "state", "step", "updated_at")
    list_filter = ("tenant", "bot_type", "state")
    readonly_fields = ("created_at", "updated_at")

