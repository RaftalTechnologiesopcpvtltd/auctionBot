"""URL configuration for AuctionBot project."""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("super-admin/", include("apps.tenants.super_admin_urls")),
    path("", include("apps.core.urls")),
    path("telegram/", include("apps.telegram_engine.urls")),
    path("dashboard/", include("apps.dashboard.urls")),
]
