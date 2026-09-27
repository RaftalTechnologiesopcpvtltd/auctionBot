from apps.tenants.models import Tenant
from apps.tenants.security import is_platform_admin, get_user_tenants
from apps.telegram_engine.models import TelegramBotConfig
from apps.listings.models import Listing, ListingStatus


def dashboard_context(request):
    """
    Context processor providing active tenant resolution, authorized tenants list,
    can_switch_tenant capability flag, bot connection status, and unread alerts.
    """
    if not request.path.startswith("/dashboard/"):
        return {}

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    platform_admin = is_platform_admin(user)

    # Only platform admins see other tenants in switcher; normal tenant users see ONLY their own
    if platform_admin:
        available_tenants = list(Tenant.objects.filter(is_active=True).order_by("name"))
    else:
        user_tenants = list(get_user_tenants(user).order_by("name"))
        available_tenants = user_tenants if user_tenants else []

    active_tenant = getattr(request, "tenant", None)
    if not active_tenant and available_tenants:
        active_tenant = available_tenants[0]

    bot_connected = False
    bot_config = None
    pending_count = 0

    if active_tenant:
        bot_config = TelegramBotConfig.objects.filter(tenant=active_tenant, is_active=True).first()
        bot_connected = bot_config is not None
        pending_count = Listing.objects.filter(tenant=active_tenant, status=ListingStatus.PENDING).count()

    return {
        "active_tenant": active_tenant,
        "available_tenants": available_tenants,
        "can_switch_tenant": platform_admin,
        "is_platform_admin": platform_admin,
        "bot_connected": bot_connected,
        "bot_config": bot_config,
        "unread_alerts_count": pending_count,
        "dashboard_version": "v1.0.0",
    }
