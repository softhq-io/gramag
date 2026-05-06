"""Gramag — Auth API router."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from db import db
from db_helpers import result_single
from auth import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
def login(req: LoginRequest):
    result = db.query(
        "MATCH (u:User {username: $username}) RETURN u.username AS username, "
        "u.password_hash AS password_hash, u.role AS role, u.name AS name",
        {"username": req.username},
    )
    user = result_single(result)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access = create_access_token(user["username"], user["role"])
    refresh = create_refresh_token(user["username"])
    return {
        "access_token": access,
        "refresh_token": refresh,
        "user": {
            "username": user["username"],
            "role": user["role"],
            "name": user["name"],
        },
    }


@router.post("/refresh")
def refresh(req: RefreshRequest):
    payload = decode_token(req.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    # Verify user still exists
    result = db.query(
        "MATCH (u:User {username: $username}) RETURN u.role AS role",
        {"username": payload["sub"]},
    )
    user = result_single(result)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    access = create_access_token(payload["sub"], user["role"])
    return {"access_token": access}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    result = db.query(
        "MATCH (u:User {username: $username}) "
        "RETURN u.username AS username, u.role AS role, u.name AS name",
        {"username": current_user["username"]},
    )
    user = result_single(result)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
