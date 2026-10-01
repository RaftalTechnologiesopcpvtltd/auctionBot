"""URL configuration for AuctionBot project."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("super-admin/", include("apps.tenants.super_admin_urls")),
    path("", include("apps.core.urls")),
    path("telegram/", include("apps.telegram_engine.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
]

if settings.DEBUG or getattr(settings, "SERVE_MEDIA", False):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

