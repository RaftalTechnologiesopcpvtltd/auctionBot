"""Asynchronous Celery tasks for auction lifecycle management."""
from datetime import datetime
import logging
from typing import Any, Dict, Optional
from celery import shared_task
from django.utils.dateparse import parse_datetime
from apps.tenants.models import Tenant
from apps.bidding.models import Auction
from services.auctions import close_auction

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="bidding.close_auction_task",
)
def close_auction_task(
    self,
    auction_id: int,
    tenant_id: int,
    as_of_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Asynchronously closes an expired auction with idempotency and retry protection."""
    logger.info("Starting close_auction_task for Auction #%s (Tenant #%s)", auction_id, tenant_id)
    try:
        tenant = Tenant.objects.get(id=tenant_id)
        as_of = parse_datetime(as_of_iso) if as_of_iso else None

        auction = close_auction(
            auction_or_id=auction_id,
            as_of=as_of,
            tenant=tenant,
        )

        logger.info(
            "Auction #%s closed successfully. Final status: '%s', Winner: '%s', Price: '%s'",
            auction.id,
            auction.status,
            auction.winner_id,
            auction.winning_price,
        )
        return {
            "status": "ok",
            "auction_id": auction.id,
            "auction_status": auction.status,
            "winner_id": auction.winner_id,
            "winning_price": str(auction.winning_price) if auction.winning_price else None,
        }
    except Exception as exc:
        logger.error(
            "Error closing Auction #%s on attempt %s/%s: %s",
            auction_id,
            self.request.retries + 1,
            self.max_retries,
            exc,
            exc_info=True,
        )
        # Retry only if transient error (e.g. operational DB error)
        raise self.retry(exc=exc)
