"""Local email/password authentication API."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from auth import (
    _validated_token_user,
    create_access_token,
    create_password_change_token,
    create_refresh_token,
    get_current_user,
    load_user,
    normalize_email,
    public_user,
    verify_password,
)
from config import AUTH_COOKIE_SECURE
from db import db
from user_service import change_password, now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])
_attempts: dict[str, deque[float]] = defaultdict(deque)
LOGIN_FAILURE_LIMIT = 5
LOGIN_COOLDOWN_SECONDS = 15 * 60


class LoginRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    password_change_token: str
    new_password: str


def _set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "gramag_access_token",
        token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=8 * 60 * 60,
    )


def _rate_limit_key(request: Request, identifier: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{normalize_email(identifier)}"


def _check_rate_limit(request: Request, identifier: str) -> None:
    key = _rate_limit_key(request, identifier)
    now = time.monotonic()
    attempts = _attempts[key]
    while attempts and attempts[0] <= now - LOGIN_COOLDOWN_SECONDS:
        attempts.popleft()
    if len(attempts) >= LOGIN_FAILURE_LIMIT:
        retry_after = max(1, ceil(attempts[0] + LOGIN_COOLDOWN_SECONDS - now))
        _raise_login_cooldown(retry_after)


def _record_rate_failure(request: Request, identifier: str) -> int:
    key = _rate_limit_key(request, identifier)
    now = time.monotonic()
    attempts = _attempts[key]
    while attempts and attempts[0] <= now - LOGIN_COOLDOWN_SECONDS:
        attempts.popleft()
    attempts.append(now)
    if len(attempts) < LOGIN_FAILURE_LIMIT:
        return 0
    return max(1, ceil(attempts[0] + LOGIN_COOLDOWN_SECONDS - now))


def _lockout_remaining_seconds(user: dict) -> int:
    value = user.get("locked_until")
    if not value:
        return 0
    try:
        remaining = (
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            - datetime.now(timezone.utc)
        ).total_seconds()
        return max(0, ceil(remaining))
    except ValueError:
        return 0


def _raise_login_cooldown(retry_after: int) -> None:
    retry_after = max(1, int(retry_after))
    raise HTTPException(
        status_code=429,
        detail={"code": "login_cooldown", "retry_after": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


def _record_failure(user: dict) -> int:
    previous_lock_expired = bool(user.get("locked_until")) and not _lockout_remaining_seconds(user)
    failures = 1 if previous_lock_expired else int(user.get("failed_login_count") or 0) + 1
    locked_until = None
    retry_after = 0
    if failures >= LOGIN_FAILURE_LIMIT:
        retry_after = LOGIN_COOLDOWN_SECONDS
        locked_until = datetime.fromtimestamp(
            time.time() + LOGIN_COOLDOWN_SECONDS, timezone.utc
        ).isoformat()
    db.write(
        """
        MATCH (u:User {id: $id})
        SET u.failed_login_count = $failures, u.locked_until = $locked_until
        """,
        {"id": user["id"], "failures": failures, "locked_until": locked_until},
    )
    return retry_after


def _normal_login_response(response: Response, user: dict) -> dict:
    access = create_access_token(user)
    refresh = create_refresh_token(user)
    _set_access_cookie(response, access)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "password_change_required": False,
        "user": public_user(user),
    }


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    identifier = req.email or req.username or ""
    _check_rate_limit(request, identifier)
    user = load_user(identifier, include_credentials=True)
    if not user or not user.get("active"):
        retry_after = _record_rate_failure(request, identifier)
        if retry_after:
            _raise_login_cooldown(retry_after)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    lockout_remaining = _lockout_remaining_seconds(user)
    if lockout_remaining:
        _record_rate_failure(request, identifier)
        _raise_login_cooldown(lockout_remaining)
    if not verify_password(req.password, user.get("password_hash") or ""):
        rate_retry_after = _record_rate_failure(request, identifier)
        account_retry_after = _record_failure(user)
        if rate_retry_after or account_retry_after:
            _raise_login_cooldown(max(rate_retry_after, account_retry_after))
        raise HTTPException(status_code=401, detail="Invalid credentials")
    db.write(
        """
        MATCH (u:User {id: $id})
        SET u.failed_login_count = 0, u.locked_until = NULL, u.last_login_at = $now
        """,
        {"id": user["id"], "now": now_iso()},
    )
    user["failed_login_count"] = 0
    user["locked_until"] = None
    _attempts.pop(_rate_limit_key(request, identifier), None)
    if user.get("must_change_password"):
        return {
            "password_change_required": True,
            "password_change_token": create_password_change_token(user),
            "user": public_user(user),
        }
    return _normal_login_response(response, user)


@router.post("/change-password")
def initial_password_change(req: PasswordChangeRequest, response: Response):
    user, _ = _validated_token_user(req.password_change_token, "password_change")
    if not user.get("must_change_password"):
        raise HTTPException(status_code=400, detail="Password change is not required")
    updated = change_password(user["id"], req.new_password)
    return _normal_login_response(response, updated)


@router.post("/refresh")
def refresh(req: RefreshRequest, response: Response):
    user, _ = _validated_token_user(req.refresh_token, "refresh")
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Password change required")
    access = create_access_token(user)
    _set_access_cookie(response, access)
    return {"access_token": access}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("gramag_access_token", path="/")
    return {"ok": True}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return public_user(current_user)
