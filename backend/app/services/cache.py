import json
import redis.asyncio as redis
from typing import Dict, Any, Optional
import os

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

CACHE_TTL_SECONDS = 300


def revenue_cache_key(property_id: str, tenant_id: str, period: str = "all") -> str:
    """Build a tenant-scoped revenue cache key.

    tenant_id must be part of the key: property IDs are only unique per tenant
    (see the composite primary key on properties), so sharing a key by property
    alone leaks one client's totals to another client.
    """
    # v3 entries carry exact integer cents; v2 entries had float-prone totals.
    return f"revenue:v3:{tenant_id}:{property_id}:{period}"


async def invalidate_revenue_cache(property_id: str, tenant_id: str, period: str = "all") -> None:
    """Remove a cached revenue summary (and the old, unsafe key format)."""
    await redis_client.delete(revenue_cache_key(property_id, tenant_id, period))
    await redis_client.delete(f"revenue:{property_id}")


async def get_revenue_summary(
    property_id: str,
    tenant_id: str,
    period: str = "all",
    month: Optional[int] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.

    Cached payloads are verified against the requesting tenant and property
    before being returned, so a poisoned or legacy entry can never be served to
    a different client.
    """
    if period != "all" and month is None and year is None:
        try:
            year_string, month_string = period.split("-")
            year, month = int(year_string), int(month_string)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid revenue period: {period}") from exc

    cache_key = revenue_cache_key(property_id, tenant_id, period)

    cached = await redis_client.get(cache_key)
    if cached:
        try:
            cached_data = json.loads(cached)
            if (
                cached_data.get("tenant_id") == tenant_id
                and cached_data.get("property_id") == property_id
                and cached_data.get("period", "all") == period
            ):
                return cached_data
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        # Never serve an unverified entry; drop it and recompute below.
        await redis_client.delete(cache_key)

    # Revenue calculation is delegated to the reservation service.
    from app.services.reservations import calculate_total_revenue

    result = await calculate_total_revenue(property_id, tenant_id, month=month, year=year)
    result["period"] = period

    # Cache the result for 5 minutes under the tenant-scoped key.
    await redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))

    # Clean up the legacy tenant-blind key if it still exists.
    await redis_client.delete(f"revenue:{property_id}")

    return result
