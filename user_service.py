"""Persistent local-user lifecycle and client assignments."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from auth import GLOBAL_ROLES, VALID_ROLES, hash_password, load_user, normalize_email, public_user
from db import db
from db_helpers import result_single, result_to_dicts, result_value

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
MIN_PASSWORD_LENGTH = 12


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_email(email: str) -> str:
    normalized = normalize_email(email)
    if not EMAIL_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="A valid email address is required")
    return normalized


def validate_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail=(
                "Username must contain 3–64 letters, numbers, dots, "
                "underscores, or hyphens"
            ),
        )
    return normalized


def validate_identity(email: str | None, username: str | None) -> tuple[str | None, str | None, str]:
    email_value = (email or "").strip()
    username_value = (username or "").strip()
    if bool(email_value) == bool(username_value):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one email address or username",
        )
    if email_value:
        normalized_email = validate_email(email_value)
        return normalized_email, None, normalized_email
    normalized_username = validate_username(username_value)
    return None, normalized_username, normalized_username


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Password must contain at least {MIN_PASSWORD_LENGTH} characters",
        )


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(18)


def _validate_clients(client_ids: list[str]) -> list[str]:
    requested = sorted(set(client_ids))
    if not requested:
        return []
    rows = result_to_dicts(
        db.query(
            "MATCH (c:Customer) WHERE c.erp_id IN $ids RETURN c.erp_id AS id",
            {"ids": requested},
        )
    )
    found = {row["id"] for row in rows}
    missing = [client_id for client_id in requested if client_id not in found]
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown client IDs: {', '.join(missing)}")
    return requested


def _audit(actor_id: str, target_id: str, action: str, details: dict | None = None) -> None:
    db.write(
        """
        CREATE (:UserAuditEvent {
          id: $id, actor_user_id: $actor, target_user_id: $target,
          action: $action, details_json: $details, created_at: $created_at
        })
        """,
        {
            "id": f"audit_{uuid.uuid4().hex}",
            "actor": actor_id,
            "target": target_id,
            "action": action,
            "details": json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            "created_at": now_iso(),
        },
    )


def _replace_assignments(user_id: str, role: str, client_ids: list[str]) -> None:
    db.write(
        "MATCH (u:User {id: $id})-[r:CAN_ACCESS]->(:Customer) DELETE r",
        {"id": user_id},
    )
    if role == "user" and client_ids:
        db.write(
            """
            MATCH (u:User {id: $id}), (c:Customer)
            WHERE c.erp_id IN $client_ids
            MERGE (u)-[:CAN_ACCESS]->(c)
            """,
            {"id": user_id, "client_ids": client_ids},
        )


def serialize_user(user: dict) -> dict:
    return {**public_user(user), "client_ids": list(user.get("client_ids") or [])}


def list_users() -> list[dict]:
    rows = result_to_dicts(
        db.query(
            """
            MATCH (u:User)
            OPTIONAL MATCH (u)-[:CAN_ACCESS]->(c:Customer)
            RETURN u.id AS id, u.email AS email, u.username AS username,
                   coalesce(u.username, u.email) AS identifier,
                   coalesce(u.name, u.username) AS name, u.role AS role,
                   coalesce(u.active, true) AS active,
                   coalesce(u.must_change_password, false) AS must_change_password,
                   u.created_at AS created_at, u.updated_at AS updated_at,
                   u.last_login_at AS last_login_at,
                   collect(DISTINCT c.erp_id) AS client_ids
            """
        )
    )
    for row in rows:
        row["client_ids"] = [client_id for client_id in (row.get("client_ids") or []) if client_id]
    rows.sort(
        key=lambda row: str(
            row.get("name") or row.get("identifier") or ""
        ).casefold()
    )
    return rows


def list_clients() -> list[dict]:
    return result_to_dicts(
        db.query(
            """
            MATCH (c:Customer)
            OPTIONAL MATCH (c)-[:OWNS]->(m:Machine)
            RETURN c.erp_id AS id, c.name AS name, count(DISTINCT m) AS machine_count
            ORDER BY toLower(c.name)
            """
        )
    )


def create_user(
    *,
    name: str,
    role: str,
    client_ids: list[str],
    actor_id: str,
    email: str | None = None,
    username: str | None = None,
) -> tuple[dict, str]:
    normalized_email, normalized_username, identifier = validate_identity(email, username)
    if role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    existing = result_single(
        db.query(
            """
            MATCH (u:User)
            WHERE u.login_normalized = $identifier
               OR u.email_normalized = $identifier
               OR u.username_normalized = $identifier
               OR toLower(u.username) = $identifier
            RETURN u.id AS id
            """,
            {"identifier": identifier},
        )
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A user with this email or username already exists",
        )
    grants = _validate_clients(client_ids) if role == "user" else []
    temporary_password = generate_temporary_password()
    user_id = f"user_{uuid.uuid4().hex}"
    now = now_iso()
    created = result_single(db.write(
        """
        MERGE (u:User {login_normalized: $identifier})
        ON CREATE SET u.id = $id, u.email = $email,
                      u.email_normalized = $email, u.username = $username,
                      u.username_normalized = $username, u.name = $name,
                      u.role = $role, u.password_hash = $password_hash,
                      u.active = true, u.must_change_password = true,
                      u.auth_version = 1, u.failed_login_count = 0,
                      u.created_at = $now, u.updated_at = $now
        RETURN u.id AS id
        """,
        {
            "id": user_id,
            "identifier": identifier,
            "email": normalized_email,
            "username": normalized_username,
            "name": name.strip() or identifier,
            "role": role,
            "password_hash": hash_password(temporary_password),
            "now": now,
        },
    ))
    if not created or created.get("id") != user_id:
        raise HTTPException(
            status_code=409,
            detail="A user with this email or username already exists",
        )
    _replace_assignments(user_id, role, grants)
    _audit(
        actor_id,
        user_id,
        "user_created",
        {
            "identifier": identifier,
            "identity_type": "email" if normalized_email else "username",
            "role": role,
            "client_ids": grants,
        },
    )
    return serialize_user(load_user(user_id) or {}), temporary_password


def _active_superadmin_count(excluding_id: str | None = None) -> int:
    return int(
        result_value(
            db.query(
                """
                MATCH (u:User {role: 'superadmin'})
                WHERE coalesce(u.active, true) AND ($excluding IS NULL OR u.id <> $excluding)
                RETURN count(u) AS count
                """,
                {"excluding": excluding_id},
            ),
            "count",
            0,
        )
    )


def update_user(user_id: str, changes: dict, *, actor_id: str) -> dict:
    current = load_user(user_id)
    if not current:
        raise HTTPException(status_code=404, detail="User not found")
    role = changes.get("role", current["role"])
    active = changes.get("active", current["active"])
    if role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="Invalid role")
    if current["role"] == "superadmin" and (role != "superadmin" or not active):
        if _active_superadmin_count(excluding_id=user_id) == 0:
            raise HTTPException(status_code=409, detail="The last active superadmin cannot be changed")
    client_ids = changes.get("client_ids", current.get("client_ids") or [])
    grants = _validate_clients(client_ids) if role == "user" else []
    name = str(
        changes.get("name", current.get("name") or current["identifier"])
    ).strip()
    db.write(
        """
        MATCH (u:User {id: $id})
        SET u.name = $name, u.role = $role, u.active = $active,
            u.updated_at = $now, u.auth_version = coalesce(u.auth_version, 0) + 1
        """,
        {"id": user_id, "name": name, "role": role, "active": bool(active), "now": now_iso()},
    )
    _replace_assignments(user_id, role, grants)
    _audit(
        actor_id,
        user_id,
        "user_updated",
        {"name": name, "role": role, "active": bool(active), "client_ids": grants},
    )
    return serialize_user(load_user(user_id) or {})


def reset_password(user_id: str, *, actor_id: str) -> str:
    user = load_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    temporary_password = generate_temporary_password()
    db.write(
        """
        MATCH (u:User {id: $id})
        SET u.password_hash = $hash, u.must_change_password = true,
            u.failed_login_count = 0, u.locked_until = NULL,
            u.auth_version = coalesce(u.auth_version, 0) + 1, u.updated_at = $now
        """,
        {"id": user_id, "hash": hash_password(temporary_password), "now": now_iso()},
    )
    _audit(actor_id, user_id, "password_reset")
    return temporary_password


def change_password(user_id: str, password: str) -> dict:
    validate_password(password)
    db.write(
        """
        MATCH (u:User {id: $id})
        SET u.password_hash = $hash, u.must_change_password = false,
            u.failed_login_count = 0, u.locked_until = NULL,
            u.auth_version = coalesce(u.auth_version, 0) + 1, u.updated_at = $now
        """,
        {"id": user_id, "hash": hash_password(password), "now": now_iso()},
    )
    return load_user(user_id) or {}
