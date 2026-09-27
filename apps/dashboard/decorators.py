from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from apps.tenants.models import Tenant


def dashboard_auth_required(view_func):
    """
    Decorator requiring user authentication and staff/admin role to access dashboard pages.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/dashboard/login/?next={request.path}")
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, "Access restricted: Staff or Administrator permissions required.")
            return redirect("/dashboard/login/")

        # Ensure active_tenant is available on request
        active_tenant = None
        tenant_id = request.session.get("active_tenant_id")
        if tenant_id:
            active_tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()

        if not active_tenant:
            active_tenant = Tenant.objects.filter(is_active=True).first()
            if active_tenant:
                request.session["active_tenant_id"] = active_tenant.id

        request.tenant = active_tenant
        return view_func(request, *args, **kwargs)

    return _wrapped_view
