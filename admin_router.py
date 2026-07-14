"""Superadmin-only user and client management API."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from auth import require_superadmin
from user_service import create_user, list_clients, list_users, reset_password, update_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserCreateRequest(BaseModel):
    email: str
    name: str
    role: str
    client_ids: list[str] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    client_ids: list[str] | None = None


@router.get("/users")
def users(_admin: dict = Depends(require_superadmin)):
    return list_users()


@router.post("/users")
def add_user(req: UserCreateRequest, admin: dict = Depends(require_superadmin)):
    user, temporary_password = create_user(
        email=req.email,
        name=req.name,
        role=req.role,
        client_ids=req.client_ids,
        actor_id=admin["id"],
    )
    return {"user": user, "temporary_password": temporary_password}


@router.patch("/users/{user_id}")
def edit_user(
    user_id: str,
    req: UserUpdateRequest,
    admin: dict = Depends(require_superadmin),
):
    changes = req.model_dump(exclude_unset=True)
    return update_user(user_id, changes, actor_id=admin["id"])


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, admin: dict = Depends(require_superadmin)):
    return {"temporary_password": reset_password(user_id, actor_id=admin["id"])}


@router.get("/clients")
def clients(_admin: dict = Depends(require_superadmin)):
    return list_clients()

