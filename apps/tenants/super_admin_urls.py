"""URL routing for Platform Super Admin Portal."""
from django.urls import path
from apps.tenants import super_admin_views as views

app_name = "super_admin"

urlpatterns = [
    path("", views.super_admin_dashboard_view, name="dashboard"),
    path("login/", views.super_admin_login_view, name="login"),
    path("logout/", views.super_admin_logout_view, name="logout"),
    path("tenants/register/", views.super_admin_register_tenant, name="register_tenant"),
    path("tenants/<int:tenant_id>/toggle/", views.super_admin_toggle_tenant, name="toggle_tenant"),
    path("tenants/<int:tenant_id>/reset-password/", views.super_admin_reset_tenant_password, name="reset_tenant_password"),
    path("bots/", views.super_admin_bots_view, name="bots"),

    path("bots/<int:bot_id>/update/", views.super_admin_update_bot, name="update_bot"),
    path("bots/<int:bot_id>/test/", views.super_admin_test_bot, name="test_bot"),
    path("bots/<int:bot_id>/set-webhook/", views.super_admin_set_webhook, name="set_webhook"),
]

