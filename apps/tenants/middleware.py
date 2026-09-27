"""Subdomain-based Multi-Tenant resolution and isolation middleware."""
import logging
from typing import Optional
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import render
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


class SubdomainTenantMiddleware:
    """
    Resolves tenant context dynamically from the HTTP Host header.

    Supported routing:
    - admin.auctionbot.shop / admin.localhost -> Platform Super Admin Portal
    - <tenant_slug>.auctionbot.shop / <tenant_slug>.localhost -> Tenant-isolated Portal
    - 72.62.248.151 / localhost / 127.0.0.1 -> Direct IP / Local development routing
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        host = request.get_host().split(":")[0].lower()
        subdomain = self._extract_subdomain(host)

        request.subdomain = subdomain
        request.is_super_admin_host = False
        request.tenant = None

        # 1. Super Admin Portal routing
        if subdomain == "admin" or request.path_info.startswith("/super-admin/"):
            request.is_super_admin_host = True
            if subdomain == "admin" and request.path_info == "/":
                return HttpResponseRedirect("/super-admin/")

        # 2. Specific Tenant Subdomain routing (<tenant_slug>.auctionbot.shop)
        elif subdomain:
            tenant = Tenant.objects.filter(slug__iexact=subdomain).first()
            if not tenant:
                return render(
                    request,
                    "dashboard/tenant_not_found.html",
                    {"subdomain": subdomain, "host": host},
                    status=404,
                )
            if not tenant.is_active:
                return render(
                    request,
                    "dashboard/tenant_inactive.html",
                    {"tenant": tenant},
                    status=403,
                )
            request.tenant = tenant
            # Store in session for consistency
            if hasattr(request, "session"):
                request.session["active_tenant_id"] = tenant.id

            if request.path_info == "/":
                return HttpResponseRedirect("/dashboard/")

        # 3. Direct IP / Root domain access without subdomain (e.g. 72.62.248.151)
        else:
            # Query param override or session fallback
            tenant_slug_query = request.GET.get("tenant")
            if tenant_slug_query:
                tenant = Tenant.objects.filter(slug__iexact=tenant_slug_query, is_active=True).first()
                if tenant:
                    request.tenant = tenant
                    if hasattr(request, "session"):
                        request.session["active_tenant_id"] = tenant.id
            elif hasattr(request, "session") and request.session.get("active_tenant_id"):
                session_tenant_id = request.session.get("active_tenant_id")
                request.tenant = Tenant.objects.filter(id=session_tenant_id, is_active=True).first()

            if request.path_info == "/":
                if request.is_super_admin_host:
                    return HttpResponseRedirect("/super-admin/")
                return HttpResponseRedirect("/dashboard/")

        return self.get_response(request)

    @staticmethod
    def _extract_subdomain(host: str) -> Optional[str]:
        """Extract the leftmost subdomain part if applicable."""
        # IP addresses (e.g. 72.62.248.151, 127.0.0.1) have no subdomains
        parts = host.split(".")
        if all(part.isdigit() for part in parts):
            return None

        # auctionbot.shop domains
        if host.endswith("auctionbot.shop"):
            prefix = host[:-len("auctionbot.shop")].rstrip(".")
            if prefix and "." not in prefix:
                return prefix
            elif prefix:
                return prefix.split(".")[-1]
            return None

        # localhost testing (e.g. cyg.localhost, admin.localhost)
        if host.endswith("localhost") and len(parts) > 1:
            return parts[0]

        # General domain with 3 or more parts (e.g. cyg.example.com)
        if len(parts) >= 3:
            return parts[0]

        return None
