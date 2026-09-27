"""Super Admin Portal Views for Multi-Tenant Management and Dual-Bot Administration."""
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST
from functools import wraps

from apps.tenants.models import Tenant, TenantMembership, TenantRole
from apps.tenants.security import is_platform_admin
from apps.telegram_engine.models import TelegramBotConfig, BotType
from services.telegram import TelegramService

logger = logging.getLogger(__name__)


def super_admin_required(view_func):
    """Decorator ensuring that only Platform Administrators or Superusers can access."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/super-admin/login/?next={request.path}")
        if not (request.user.is_superuser or is_platform_admin(request.user)):
            messages.error(request, "Access denied: Platform Administrator privileges required.")
            return redirect("/super-admin/login/")
        return view_func(request, *args, **kwargs)
    return _wrapped


def super_admin_login_view(request):
    """Platform Super Admin login page."""
    if request.user.is_authenticated and (request.user.is_superuser or is_platform_admin(request.user)):
        return redirect("/super-admin/")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        user = authenticate(request, username=username, password=password)

        if user is not None and (user.is_superuser or is_platform_admin(user)):
            login(request, user)
            next_url = request.GET.get("next") or request.POST.get("next") or "super_admin:dashboard"
            return redirect(next_url)
        else:
            messages.error(request, "Invalid credentials or account lacks Platform Administrator privileges.")

    return render(request, "super_admin/login.html")


def super_admin_logout_view(request):
    """Logs out super admin and returns to login."""
    logout(request)
    messages.info(request, "Super Admin session closed.")
    return redirect("super_admin:login")


@super_admin_required
def super_admin_dashboard_view(request):
    """Main Super Admin Dashboard: tenant registration, visible passwords, tenant directory."""
    tenants = Tenant.objects.all().order_by("-created_at")

    # Attach dual bot info to each tenant
    tenant_cards = []
    for t in tenants:
        seller_bot = TelegramBotConfig.objects.filter(tenant=t, bot_type=BotType.SELLER).first()
        bidding_bot = TelegramBotConfig.objects.filter(tenant=t, bot_type=BotType.BUYER).first()
        user_count = TenantMembership.objects.filter(tenant=t, is_active=True).count()
        tenant_cards.append({
            "tenant": t,
            "seller_bot": seller_bot,
            "bidding_bot": bidding_bot,
            "user_count": user_count,
        })

    total_tenants = tenants.count()
    active_tenants = tenants.filter(is_active=True).count()
    configured_bots = TelegramBotConfig.objects.filter(is_active=True).exclude(token_encrypted="").count()
    total_users = User.objects.count()

    return render(request, "super_admin/dashboard.html", {
        "tenant_cards": tenant_cards,
        "total_tenants": total_tenants,
        "active_tenants": active_tenants,
        "configured_bots": configured_bots,
        "total_users": total_users,
    })


@super_admin_required
@require_POST
def super_admin_register_tenant(request):
    """Handles tenant registration and automatic dual bot initialization."""
    name = request.POST.get("name", "").strip()
    slug = request.POST.get("slug", "").strip().lower()
    code = request.POST.get("code", "").strip().upper()
    country = request.POST.get("country", "").strip() or "Malaysia"
    currency = request.POST.get("currency", "MYR").strip().upper()
    timezone = request.POST.get("timezone", "Asia/Kuala_Lumpur").strip()
    admin_username = request.POST.get("admin_username", "").strip()
    admin_password = request.POST.get("admin_password", "").strip()

    if not name or not slug or not code or not admin_username or not admin_password:
        messages.error(request, "Please fill in all required tenant details and administrator credentials.")
        return redirect("/super-admin/")

    if Tenant.objects.filter(slug=slug).exists():
        messages.error(request, f"Subdomain '{slug}.auctionbot.shop' is already registered.")
        return redirect("/super-admin/")

    if Tenant.objects.filter(code=code).exists():
        messages.error(request, f"Region code '{code}' is already registered.")
        return redirect("/super-admin/")

    try:
        # 1. Create Tenant
        tenant = Tenant.objects.create(
            name=name,
            slug=slug,
            code=code,
            country=country,
            currency=currency,
            timezone=timezone,
            admin_username=admin_username,
            admin_initial_password=admin_password,
            is_active=True,
        )

        # 2. Provision Tenant Admin User
        user, _ = User.objects.get_or_create(
            username=admin_username,
            defaults={"email": f"{admin_username}@{slug}.auctionbot.shop", "is_staff": True}
        )
        user.set_password(admin_password)
        user.is_staff = True
        user.save()

        # 3. Create Tenant Membership
        TenantMembership.objects.create(
            user=user,
            tenant=tenant,
            role=TenantRole.TENANT_ADMIN,
            is_active=True,
        )

        # 4. Provision Dual Bot Configs (Seller Bot & Bidding Bot)
        seller_bot, bidding_bot = TelegramBotConfig.ensure_dual_bots(tenant)

        # Optional initial bot tokens from registration form
        seller_token = request.POST.get("seller_bot_token", "").strip()
        seller_handle = request.POST.get("seller_bot_username", "").strip().lstrip("@")
        if seller_token:
            seller_bot.set_token(seller_token)
        if seller_handle:
            seller_bot.bot_username = seller_handle
        seller_bot.save()

        bidding_token = request.POST.get("bidding_bot_token", "").strip()
        bidding_handle = request.POST.get("bidding_bot_username", "").strip().lstrip("@")
        if bidding_token:
            bidding_bot.set_token(bidding_token)
        if bidding_handle:
            bidding_bot.bot_username = bidding_handle
        bidding_bot.save()

        messages.success(
            request,
            f"Tenant '{name}' registered successfully! Subdomain: {slug}.auctionbot.shop (Admin: {admin_username})"
        )
    except Exception as exc:
        logger.exception("Failed to register tenant: %s", exc)
        messages.error(request, f"Error registering tenant: {exc}")

    return redirect("super_admin:dashboard")


@super_admin_required
@require_POST
def super_admin_toggle_tenant(request, tenant_id):
    """Toggles active/suspended state for a tenant."""
    tenant = get_object_or_404(Tenant, id=tenant_id)
    tenant.is_active = not tenant.is_active
    tenant.save(update_fields=["is_active", "updated_at"])
    status_str = "activated" if tenant.is_active else "suspended"
    messages.success(request, f"Tenant '{tenant.name}' has been {status_str}.")
    return redirect("super_admin:dashboard")


@super_admin_required
def super_admin_bots_view(request):
    """Dual-Bot Management Center for all tenants (Seller & Bidding Bots)."""
    tenants = Tenant.objects.all().order_by("name")

    bot_rows = []
    for t in tenants:
        seller_bot, _ = TelegramBotConfig.objects.get_or_create(
            tenant=t,
            bot_type=BotType.SELLER,
            defaults={"is_active": True}
        )
        bidding_bot, _ = TelegramBotConfig.objects.get_or_create(
            tenant=t,
            bot_type=BotType.BUYER,
            defaults={"is_active": True}
        )
        bot_rows.append({
            "tenant": t,
            "seller_bot": seller_bot,
            "bidding_bot": bidding_bot,
            "seller_webhook": f"http://{t.slug}.auctionbot.shop{seller_bot.webhook_path}",
            "bidding_webhook": f"http://{t.slug}.auctionbot.shop{bidding_bot.webhook_path}",
        })

    return render(request, "super_admin/bots.html", {
        "bot_rows": bot_rows,
    })


@super_admin_required
@require_POST
def super_admin_update_bot(request, bot_id):
    """Updates bot configuration parameters."""
    bot = get_object_or_404(TelegramBotConfig, id=bot_id)
    bot_username = request.POST.get("bot_username", "").strip().lstrip("@")
    plain_token = request.POST.get("bot_token", "").strip()
    webhook_secret = request.POST.get("webhook_secret_token", "").strip()
    is_active = request.POST.get("is_active") == "on"

    bot.bot_username = bot_username
    bot.webhook_secret_token = webhook_secret
    bot.is_active = is_active
    if plain_token and not plain_token.startswith("••"):
        bot.set_token(plain_token)
    bot.save()

    messages.success(request, f"{bot.get_bot_type_display()} for '{bot.tenant.name}' updated successfully.")
    return redirect("super_admin:bots")


@super_admin_required
def super_admin_test_bot(request, bot_id):
    """Tests bot token connectivity via Telegram API getMe."""
    bot = get_object_or_404(TelegramBotConfig, id=bot_id)
    token = bot.get_token()
    if not token:
        return JsonResponse({"ok": False, "error": "No bot token configured."})

    try:
        service = TelegramService(bot)
        info = service.get_me()
        if info.get("ok"):
            bot_data = info.get("result", {})
            return JsonResponse({
                "ok": True,
                "username": bot_data.get("username"),
                "first_name": bot_data.get("first_name"),
            })
        else:
            return JsonResponse({"ok": False, "error": info.get("description", "Unknown Telegram API error")})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
