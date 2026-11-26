from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, Optional
from app.services.cache import get_revenue_summary
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    tenant_id = getattr(current_user, "tenant_id", "default_tenant") or "default_tenant"

    if (month is None) != (year is None):
        raise HTTPException(status_code=400, detail="month and year must be provided together")
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="month must be between 1 and 12")
    if year is not None and year < 1:
        raise HTTPException(status_code=400, detail="year must be positive")

    period = "all" if month is None else f"{year:04d}-{month:02d}"

    revenue_data = await get_revenue_summary(
        property_id, tenant_id, period=period, month=month, year=year
    )

    if (
        revenue_data.get("tenant_id") != tenant_id
        or revenue_data.get("property_id") != property_id
    ):
        raise HTTPException(
            status_code=500,
            detail="Revenue summary did not match the requested tenant/property",
        )

    # Keep money as an exact decimal string (plus integer cents) instead of a
    # binary float, which can drift by a cent during display rounding.
    return {
        "property_id": revenue_data['property_id'],
        "period": revenue_data.get("period", period),
        "total_revenue": revenue_data['total'],
        "total_revenue_cents": revenue_data.get('total_cents'),
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count']
    }
