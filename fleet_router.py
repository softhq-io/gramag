"""Gramag — Fleet Health / Predictive Maintenance API router."""

from fastapi import APIRouter, Depends, Query
from auth import get_current_user
import fleet

router = APIRouter(prefix="/api/fleet", tags=["fleet"])


@router.get("/dashboard")
def fleet_dashboard(
    customer_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None),
    _user: dict = Depends(get_current_user),
):
    return fleet.get_fleet_dashboard(customer_id, limit, offset, q)


@router.get("/customers")
def fleet_customers(_user: dict = Depends(get_current_user)):
    return fleet.get_customers_list()


@router.get("/machine/{erp_id}/mtbr")
def machine_mtbr(erp_id: str, _user: dict = Depends(get_current_user)):
    return fleet.compute_mtbr(erp_id)


@router.get("/machine/{erp_id}/risk")
def machine_risk(erp_id: str, _user: dict = Depends(get_current_user)):
    return fleet.compute_risk_score(erp_id)
