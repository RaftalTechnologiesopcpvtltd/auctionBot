from django.urls import path
from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    # Authentication & Session
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("switch-tenant/<int:tenant_id>/", views.switch_tenant_view, name="switch_tenant"),

    # Main Executive Dashboard
    path("", views.dashboard_home_view, name="home"),

    # Auctions Management
    path("auctions/", views.auctions_list_view, name="auctions_list"),
    path("auctions/<int:auction_id>/", views.auction_detail_view, name="auction_detail"),
    path("auctions/<int:auction_id>/end/", views.auction_end_action, name="auction_end"),
    path("auctions/<int:auction_id>/cancel/", views.auction_cancel_action, name="auction_cancel"),

    # Listings & Enquiries
    path("listings/", views.listings_list_view, name="listings_list"),
    path("listings/<int:listing_id>/", views.listing_detail_view, name="listing_detail"),
    path("listings/<int:listing_id>/accept/", views.listing_accept_action, name="listing_accept"),
    path("listings/<int:listing_id>/reject/", views.listing_reject_action, name="listing_reject"),

    # Bids Management
    path("bids/", views.bids_list_view, name="bids_list"),

    # Sellers & Users
    path("sellers/", views.sellers_list_view, name="sellers_list"),
    path("users/", views.users_list_view, name="users_list"),

    # Telegram Management
    path("telegram/", views.telegram_overview_view, name="telegram_overview"),
    path("telegram/settings/", views.telegram_settings_view, name="telegram_settings"),
    path("telegram/messages/", views.telegram_messages_view, name="telegram_messages"),
    path("telegram/users/", views.telegram_users_view, name="telegram_users"),

    # Wallets & Financial Ledger
    path("wallets/", views.wallets_list_view, name="wallets_list"),
    path("wallets/accounts/", views.wallets_list_view, name="finance_wallets"),
    path("wallets/deposits/<int:deposit_id>/approve/", views.deposit_approve_action, name="deposit_approve"),
    path("wallets/deposits/<int:deposit_id>/reject/", views.deposit_reject_action, name="deposit_reject"),
    path("transactions/", views.transactions_list_view, name="transactions_list"),
    path("transactions/ledger/", views.transactions_list_view, name="finance_transactions"),
    path("transactions/<int:transaction_id>/", views.transaction_detail_view, name="transaction_detail"),
    path("transactions/<int:transaction_id>/detail/", views.transaction_detail_view, name="finance_transaction_detail"),
    path("transactions/<int:transaction_id>/refund/", views.transaction_refund_action, name="transaction_refund"),
    path("transactions/<int:transaction_id>/reverse/", views.transaction_reverse_action, name="transaction_reverse"),

    # Reports, Settings & Helpdesk
    path("reports/", views.reports_view, name="reports"),
    path("settings/", views.business_settings_view, name="settings"),
    path("settings/business/", views.business_settings_view, name="settings_business"),
    path("helpdesk/", views.helpdesk_view, name="helpdesk"),
]
