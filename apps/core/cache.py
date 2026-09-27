"""Cache utilities enforcing strict multi-tenant key isolation."""
from typing import Union
from apps.tenants.models import Tenant


def get_tenant_cache_key(tenant_or_id: Union[Tenant, int, str], key_name: str) -> str:
    """Builds a tenant-isolated cache key.

    Example:
        get_tenant_cache_key(tenant, 'dashboard_stats') -> 'tenant:1:dashboard_stats'
    """
    if hasattr(tenant_or_id, "id"):
        t_id = tenant_or_id.id
    else:
        t_id = tenant_or_id
    sanitized_key = str(key_name).strip().replace(" ", "_")
    return f"tenant:{t_id}:{sanitized_key}"
