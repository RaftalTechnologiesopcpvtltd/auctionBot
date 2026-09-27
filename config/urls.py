"""URL configuration for AuctionBot project."""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("telegram/", include("apps.telegram_engine.urls")),
]
