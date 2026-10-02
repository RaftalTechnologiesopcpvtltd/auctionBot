"""Public and Seller Live Listings view replicating and modernizing legacy /live-listing/."""
from django.shortcuts import render
from django.utils import timezone
from apps.tenants.models import Tenant
from apps.listings.models import Listing, ListingStatus, ListingType
from apps.bidding.models import Auction, AuctionStatus


def live_listing_view(request, seller_id=None):
    """
    Public and Seller Live Listings portal.
    Supports /live-listing/, /live-listing/<seller_id>/, or ?seller_id=...
    """
    # 1. Resolve Tenant
    tenant = getattr(request, "tenant", None)
    if not tenant:
        tenant_slug = request.GET.get("tenant") or request.headers.get("X-Tenant-Slug")
        if tenant_slug:
            tenant = Tenant.objects.filter(slug=tenant_slug, is_active=True).first()
        
        if not tenant:
            tenant = (
                Tenant.objects.filter(slug__in=["cyg-malaysia", "cyg-my", "cyg"], is_active=True).first()
                or Tenant.objects.filter(is_active=True).first()
            )

    if not tenant:
        return render(request, "listings/live_listing.html", {"error": "No active tenant configured."})

    target_seller_id = seller_id or request.GET.get("seller_id") or request.GET.get("telegram_id")
    tab = request.GET.get("tab", "live_auction").lower()

    # 2. Fetch Live Auctions
    live_auctions_qs = Auction.objects.filter(
        tenant=tenant,
        status=AuctionStatus.ACTIVE
    ).select_related("listing").prefetch_related("listing__images", "bids").order_by("end_at")

    if target_seller_id:
        live_auctions_qs = live_auctions_qs.filter(listing__seller_id=str(target_seller_id))

    # 3. Fetch Live Buy-It-Now
    live_buynow_qs = Listing.objects.filter(
        tenant=tenant,
        listing_type=ListingType.BUY_NOW,
        status=ListingStatus.APPROVED
    ).prefetch_related("images").order_by("-created_at")

    if target_seller_id:
        live_buynow_qs = live_buynow_qs.filter(seller_id=str(target_seller_id))

    # 4. Fetch Completed Auctions & Buy It Now
    completed_auctions_qs = Auction.objects.filter(
        tenant=tenant,
        status__in=[AuctionStatus.SOLD, AuctionStatus.UNSOLD, AuctionStatus.CLOSED]
    ).select_related("listing").prefetch_related("listing__images", "bids").order_by("-end_at")

    if target_seller_id:
        completed_auctions_qs = completed_auctions_qs.filter(listing__seller_id=str(target_seller_id))

    completed_auctions_qs = completed_auctions_qs[:20]

    counts = {
        "live_auction": live_auctions_qs.count(),
        "live_buynow": live_buynow_qs.count(),
        "completed": completed_auctions_qs.count(),
    }

    context = {
        "tenant": tenant,
        "seller_id": target_seller_id,
        "tab": tab,
        "counts": counts,
        "live_auctions": live_auctions_qs,
        "live_buynow": live_buynow_qs,
        "completed_auctions": completed_auctions_qs,
        "now": timezone.now(),
    }
    return render(request, "listings/live_listing.html", context)
