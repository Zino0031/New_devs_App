import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def _next_month(year: int, month: int) -> Tuple[int, int]:
    """Return the (year, month) following the given month."""
    if month == 12:
        return year + 1, 1
    return year, month + 1


async def _month_bounds_utc(
    session: Any, property_id: str, tenant_id: str, month: int, year: int
) -> Tuple[datetime, datetime]:
    """Resolve a month's UTC boundaries in the property's local timezone.

    Properties are in different timezones (e.g. Europe/Paris and
    America/New_York). A reservation at 2024-02-29 23:30 UTC is March 1 in
    Paris, so UTC calendar boundaries would drop a March booking.
    """
    from sqlalchemy import text

    result = await session.execute(
        text("""
            SELECT timezone
            FROM properties
            WHERE property_id = :property_id AND tenant_id = :tenant_id
        """),
        {"property_id": property_id, "tenant_id": tenant_id},
    )
    row = result.fetchone()
    timezone_name = row.timezone if row and row.timezone else "UTC"

    try:
        local_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown timezone %s for %s; using UTC", timezone_name, property_id)
        local_timezone = ZoneInfo("UTC")

    next_year, next_month = _next_month(year, month)
    start_local = datetime(year, month, 1, tzinfo=local_timezone)
    end_local = datetime(next_year, next_month, 1, tzinfo=local_timezone)

    return (
        start_local.astimezone(dt_timezone.utc),
        end_local.astimezone(dt_timezone.utc),
    )


async def calculate_monthly_revenue(
    property_id: str,
    tenant_id: str,
    month: int,
    year: int,
    db_session: Optional[Any] = None,
) -> Decimal:
    """Calculate revenue for one property in a given local calendar month."""
    result = await calculate_total_revenue(property_id, tenant_id, month=month, year=year)
    return Decimal(result["total"])


async def calculate_total_revenue(
    property_id: str,
    tenant_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    """Aggregate revenue from the database, optionally for a local month.

    Never falls back to hardcoded figures: silent mock data was the reason the
    dashboard disagreed with client records. A database failure is surfaced to
    the caller instead.
    """
    if (month is None) != (year is None):
        raise ValueError("month and year must be provided together")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")

    try:
        from app.core.database_pool import db_pool

        if not db_pool.session_factory:
            await db_pool.initialize()
        if not db_pool.session_factory:
            raise RuntimeError("Database pool not available")

        from sqlalchemy import text

        async with db_pool.get_session() as session:
            params: Dict[str, Any] = {
                "property_id": property_id,
                "tenant_id": tenant_id,
            }
            date_filter = ""

            if month is not None and year is not None:
                start_utc, end_utc = await _month_bounds_utc(
                    session, property_id, tenant_id, month, year
                )
                date_filter = (
                    " AND check_in_date >= :start_utc AND check_in_date < :end_utc"
                )
                params["start_utc"] = start_utc
                params["end_utc"] = end_utc

            query = text(f"""
                SELECT
                    property_id,
                    SUM(total_amount) as total_revenue,
                    COUNT(*) as reservation_count
                FROM reservations
                WHERE property_id = :property_id AND tenant_id = :tenant_id{date_filter}
                GROUP BY property_id
            """)

            result = await session.execute(query, params)
            row = result.fetchone()

            if row:
                total_revenue = Decimal(str(row.total_revenue))
                return {
                    "property_id": property_id,
                    "tenant_id": tenant_id,
                    "total": str(total_revenue),
                    "currency": "USD",
                    "count": row.reservation_count,
                    "source": "database",
                }

            return {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "total": "0",
                "currency": "USD",
                "count": 0,
                "source": "database",
            }

    except Exception:
        logger.exception(
            "Database error for property %s (tenant %s, month=%s, year=%s)",
            property_id,
            tenant_id,
            month,
            year,
        )
        raise
