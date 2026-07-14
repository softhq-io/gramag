"""Gramag — Fleet Health / Predictive Maintenance API router."""

from fastapi import APIRouter, Depends, Query
from auth import can_access_client, get_current_user
from authorization import not_found, require_erp_machine
import fleet

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get("/dashboard")
def fleet_dashboard(
    customer_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    if customer_id and not can_access_client(user, customer_id):
        raise not_found("Customer")
    return fleet.get_fleet_dashboard(customer_id, limit, offset, q, user=user)


@router.get("/customers")
def fleet_customers(user: dict = Depends(get_current_user)):
    return fleet.get_customers_list(user=user)


@router.get("/machine/{erp_id}/mtbr")
def machine_mtbr(erp_id: str, user: dict = Depends(get_current_user)):
    require_erp_machine(user, erp_id)
    return fleet.compute_mtbr(erp_id)


@router.get("/machine/{erp_id}/risk")
def machine_risk(erp_id: str, user: dict = Depends(get_current_user)):
    require_erp_machine(user, erp_id)
    return fleet.compute_risk_score(erp_id)
