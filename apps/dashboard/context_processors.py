from apps.tenants.models import Tenant
from apps.telegram_engine.models import TelegramBotConfig
from apps.listings.models import Listing, ListingStatus


def dashboard_context(request):
    """
    Context processor providing active tenant resolution, available tenants,
    bot connection status, and unread alerts for dashboard templates.
    """
    if not request.path.startswith("/dashboard/"):
        return {}

    available_tenants = list(Tenant.objects.filter(is_active=True).order_by("name"))
    active_tenant = None

    tenant_id_in_session = request.session.get("active_tenant_id")
    if tenant_id_in_session:
        for t in available_tenants:
            if t.id == tenant_id_in_session:
                active_tenant = t
                break

    if not active_tenant and available_tenants:
        active_tenant = available_tenants[0]
        request.session["active_tenant_id"] = active_tenant.id

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
        "bot_connected": bot_connected,
        "bot_config": bot_config,
        "unread_alerts_count": pending_count or 3,
        "dashboard_version": "v1.0.0",
    }
