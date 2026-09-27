from decimal import Decimal
from django.db import migrations
from django.utils import timezone


def make_existing_listings_live(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Auction = apps.get_model("bidding", "Auction")
    now = timezone.now()

    for listing in Listing.objects.filter(listing_type="AUCTION"):
        # Ensure status is APPROVED
        if listing.status != "APPROVED":
            listing.status = "APPROVED"
            listing.save(update_fields=["status", "updated_at"])

        starting_price = Decimal("10.00")
        min_bid = Decimal("5.00")
        buy_now_price = None

        if isinstance(listing.metadata, dict):
            if listing.metadata.get("starting_price"):
                try:
                    starting_price = Decimal(str(listing.metadata["starting_price"]))
                except Exception:
                    pass
            if listing.metadata.get("min_bid"):
                try:
                    min_bid = Decimal(str(listing.metadata["min_bid"]))
                except Exception:
                    pass
            bn = listing.metadata.get("buy_now_price") or listing.metadata.get("buynow_price") or listing.metadata.get("auto_accept_price")
            if bn:
                try:
                    bn_val = Decimal(str(bn))
                    if bn_val > starting_price:
                        buy_now_price = bn_val
                except Exception:
                    pass

        auction = Auction.objects.filter(listing_id=listing.id).first()
        if not auction:
            Auction.objects.create(
                tenant_id=listing.tenant_id,
                listing_id=listing.id,
                starting_price=starting_price,
                bid_increment=min_bid,
                current_price=Decimal("0.00"),
                buy_now_price=buy_now_price,
                start_at=now - timezone.timedelta(minutes=5),
                end_at=now + timezone.timedelta(days=7),
                status="ACTIVE",
            )
        elif auction.status != "ACTIVE":
            auction.status = "ACTIVE"
            auction.save(update_fields=["status", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("bidding", "0004_buyerwishlist"),
        ("listings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(make_existing_listings_live, reverse_code=migrations.RunPython.noop),
    ]
