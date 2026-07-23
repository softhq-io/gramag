"""Authentication and authorization helpers for Gramag."""

from __future__ import annotations

import datetime
from typing import Any

import bcrypt as _bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import JWT_ACCESS_EXPIRE_HOURS, JWT_REFRESH_EXPIRE_DAYS, JWT_SECRET
from db import db
from db_helpers import result_single

security = HTTPBearer(auto_error=False)
VALID_ROLES = {"superadmin", "all_clients", "user"}
GLOBAL_ROLES = {"superadmin", "all_clients"}


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, AttributeError):
        return False


def _token(user: dict, token_type: str, expires: datetime.timedelta) -> str:
    payload = {
        "sub": user["id"],
        "email": user.get("email"),
        "username": user.get("username"),
        "identifier": user["identifier"],
        "role": user["role"],
        "ver": int(user.get("auth_version") or 0),
        "type": token_type,
        "exp": datetime.datetime.now(datetime.timezone.utc) + expires,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def create_access_token(user: dict) -> str:
    return _token(user, "access", datetime.timedelta(hours=JWT_ACCESS_EXPIRE_HOURS))


def create_refresh_token(user: dict) -> str:
    return _token(user, "refresh", datetime.timedelta(days=JWT_REFRESH_EXPIRE_DAYS))


def create_password_change_token(user: dict) -> str:
    return _token(user, "password_change", datetime.timedelta(minutes=10))


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def normalize_email(value: str) -> str:
    return value.strip().lower()


def load_user(identifier: str, *, include_credentials: bool = False) -> dict | None:
    """Load a user and current client grants from the persistent graph."""
    password_field = ", u.password_hash AS password_hash" if include_credentials else ""
    row = result_single(
        db.query(
            f"""
            MATCH (u:User)
            WHERE u.id = $identifier
               OR u.login_normalized = $normalized
               OR u.email_normalized = $normalized
               OR u.username_normalized = $normalized
               OR toLower(u.username) = $normalized
            OPTIONAL MATCH (u)-[:CAN_ACCESS]->(c:Customer)
            RETURN u.id AS id,
                   u.email AS email,
                   u.username AS username,
                   coalesce(u.username, u.email) AS identifier,
                   coalesce(u.email_normalized, toLower(u.username)) AS email_normalized,
                   coalesce(u.name, u.username, u.email) AS name,
                   u.role AS role,
                   coalesce(u.active, true) AS active,
                   coalesce(u.must_change_password, false) AS must_change_password,
                   coalesce(u.auth_version, 0) AS auth_version,
                   coalesce(u.failed_login_count, 0) AS failed_login_count,
                   u.locked_until AS locked_until,
                   collect(DISTINCT c.erp_id) AS client_ids
                   {password_field}
            """,
            {"identifier": identifier, "normalized": normalize_email(identifier)},
        )
    )
    if not row:
        return None
    row["client_ids"] = [client_id for client_id in (row.get("client_ids") or []) if client_id]
    row["all_clients"] = row.get("role") in GLOBAL_ROLES
    return row


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user.get("email"),
        "username": user.get("username"),
        "identifier": user["identifier"],
        "name": user.get("name") or user["identifier"],
        "role": user["role"],
        "active": bool(user.get("active", True)),
        "must_change_password": bool(user.get("must_change_password", False)),
    }


def _credentials_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    if credentials:
        return credentials.credentials
    return request.cookies.get("gramag_access_token")


def _validated_token_user(token: str, expected_type: str) -> tuple[dict, dict]:
    payload = decode_token(token)
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = load_user(str(payload.get("sub") or ""))
    if not user or not user.get("active"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if user.get("role") not in VALID_ROLES:
        raise HTTPException(status_code=401, detail="Invalid account role")
    if int(payload.get("ver", -1)) != int(user.get("auth_version") or 0):
        raise HTTPException(status_code=401, detail="Session revoked")
    return user, payload


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    token = _credentials_token(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    user, _ = _validated_token_user(token, "access")
    if user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="Password change required")
    return user


def get_password_change_user(token: str) -> dict:
    user, _ = _validated_token_user(token, "password_change")
    if not user.get("must_change_password"):
        raise HTTPException(status_code=400, detail="Password change is not required")
    return user


def require_superadmin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
    return current_user


def scope_params(user: dict, **extra: Any) -> dict:
    return {
        "all_clients": bool(user.get("all_clients")),
        "client_ids": list(user.get("client_ids") or []),
        **extra,
    }


def can_access_client(user: dict, client_id: str | None) -> bool:
    return bool(user.get("all_clients")) or bool(client_id and client_id in (user.get("client_ids") or []))
