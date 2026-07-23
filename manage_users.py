"""Shell-only bootstrap and recovery commands for the local user store."""

from __future__ import annotations

import argparse
import re
import sys
import uuid

from auth import hash_password, load_user
from db import db
from db_helpers import result_to_dicts, result_value
from user_service import generate_temporary_password, now_iso, validate_email


def _set_superadmin(email: str, name: str, *, require_empty: bool) -> str:
    db.connect()
    normalized = validate_email(email)
    if require_empty:
        count = result_value(
            db.query(
                "MATCH (u:User {role: 'superadmin'}) WHERE coalesce(u.active, true) RETURN count(u) AS count"
            ),
            "count",
            0,
        )
        if count:
            raise RuntimeError("An active superadmin already exists")
    temporary_password = generate_temporary_password()
    existing = load_user(normalized)
    now = now_iso()
    if existing:
        db.write(
            """
            MATCH (u:User {id: $id})
            SET u.email = $email, u.email_normalized = $email, u.name = $name,
                u.login_normalized = $email,
                u.role = 'superadmin', u.active = true,
                u.password_hash = $hash, u.must_change_password = true,
                u.failed_login_count = 0, u.locked_until = NULL,
                u.auth_version = coalesce(u.auth_version, 0) + 1, u.updated_at = $now
            """,
            {
                "id": existing["id"], "email": normalized, "name": name,
                "hash": hash_password(temporary_password), "now": now,
            },
        )
    else:
        db.write(
            """
            CREATE (:User {
              id: $id, email: $email, email_normalized: $email,
              login_normalized: $email, name: $name,
              role: 'superadmin', active: true, password_hash: $hash,
              must_change_password: true, auth_version: 1, failed_login_count: 0,
              created_at: $now, updated_at: $now
            })
            """,
            {
                "id": f"user_{uuid.uuid4().hex}", "email": normalized, "name": name,
                "hash": hash_password(temporary_password), "now": now,
            },
        )
    return temporary_password


def _parse_email_mappings(values: list[str]) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for value in values:
        username, separator, email = value.partition("=")
        username = username.strip()
        if not separator or not username:
            raise ValueError("Legacy email mappings must use USERNAME=EMAIL")
        if username in mappings:
            raise ValueError(f"Duplicate email mapping for legacy user {username}")
        mappings[username] = validate_email(email)
    return mappings


def _legacy_email(username: str, current: str | None, mappings: dict[str, str]) -> str:
    if username in mappings:
        return mappings[username]
    if current:
        try:
            return validate_email(current)
        except Exception:
            pass
    local = re.sub(r"[^a-z0-9._+-]+", "-", username.strip().lower()).strip(".-")
    return f"{local or 'user'}@legacy.invalid"


def _legacy_migration_plan(email_mappings: dict[str, str]) -> list[dict]:
    rows = result_to_dicts(
        db.query(
            """
            MATCH (u:User)
            WHERE u.username IS NOT NULL
            RETURN u.id AS id, u.username AS username, u.email AS email,
                   u.email_normalized AS email_normalized, u.role AS role,
                   u.password_hash AS password_hash
            ORDER BY u.username
            """
        )
    )
    existing_emails = {
        row["email_normalized"]
        for row in result_to_dicts(
            db.query(
                "MATCH (u:User) WHERE u.email_normalized IS NOT NULL "
                "RETURN u.email_normalized AS email_normalized"
            )
        )
        if row.get("email_normalized")
    }
    plan: list[dict] = []
    planned_emails: set[str] = set()
    legacy_usernames = {str(row["username"]) for row in rows}
    unknown_mappings = sorted(set(email_mappings) - legacy_usernames)
    if unknown_mappings:
        raise RuntimeError(
            f"No legacy user found for email mapping(s): {', '.join(unknown_mappings)}"
        )
    for row in rows:
        if row.get("id") and row.get("email_normalized") and row.get("role") in {
            "superadmin", "all_clients", "user"
        }:
            continue
        username = str(row["username"])
        if not row.get("password_hash"):
            raise RuntimeError(f"Legacy user {username} has no password hash; migration aborted")
        email = _legacy_email(username, row.get("email"), email_mappings)
        owned_current_email = row.get("email_normalized") == email
        if (email in existing_emails and not owned_current_email) or email in planned_emails:
            raise RuntimeError(f"Email {email} is already assigned; migration aborted")
        planned_emails.add(email)
        plan.append(
            {
                "id": row.get("id") or f"legacy_{uuid.uuid4().hex}",
                "username": username,
                "email": email,
                "previous_role": row.get("role"),
                "audit_id": f"audit_{uuid.uuid4().hex}",
            }
        )
    return plan


def _migrate_legacy(*, dry_run: bool, email_mappings: dict[str, str]) -> None:
    db.connect()
    count = result_value(
        db.query(
            "MATCH (u:User {role: 'superadmin'}) WHERE coalesce(u.active, true) RETURN count(u) AS count"
        ),
        "count",
        0,
    )
    if not count and not dry_run:
        raise RuntimeError("Bootstrap and verify an active superadmin before migrating legacy users")
    plan = _legacy_migration_plan(email_mappings)
    if not plan:
        print("No legacy users require migration.")
    for user in plan:
        print(
            f"{'Would migrate' if dry_run else 'Migrating'} {user['username']} "
            f"({user['previous_role'] or 'no role'} -> all_clients, email={user['email']})"
        )
    if dry_run:
        print("Dry run only: no users, chats, passwords, or business data were changed.")
        return
    now = now_iso()
    if plan:
        migrated = result_value(
            db.write(
                """
                UNWIND $users AS item
                MATCH (u:User)
                WHERE u.username = item.username
                SET u.id = item.id, u.email = item.email, u.email_normalized = item.email,
                    u.username_normalized = toLower(u.username),
                    u.login_normalized = toLower(u.username),
                    u.role = 'all_clients', u.active = true,
                    u.must_change_password = false,
                    u.failed_login_count = coalesce(u.failed_login_count, 0),
                    u.auth_version = coalesce(u.auth_version, 0) + 1,
                    u.created_at = coalesce(u.created_at, $now), u.updated_at = $now
                CREATE (:UserAuditEvent {
                  id: item.audit_id, actor_user_id: 'system:migrate-legacy',
                  target_user_id: item.id, action: 'legacy_user_migrated',
                  details_json: '{"role":"all_clients","password_hash_preserved":true}',
                  created_at: $now
                })
                RETURN count(u) AS migrated
                """,
                {"users": plan, "now": now},
            ),
            "migrated",
            0,
        )
        if int(migrated) != len(plan):
            raise RuntimeError(
                f"Expected to migrate {len(plan)} legacy users, but matched {migrated}"
            )
    from proto.db_proto import proto_db

    proto_db.write(
        """
        MATCH (s:ProtoChatSession)
        OPTIONAL MATCH (m:Machine)
        WHERE m.slug = s.machine_slug
        WITH s, m
        WHERE s.client_id IS NULL AND m.erp_customer_id IS NOT NULL
        SET s.client_id = m.erp_customer_id
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("bootstrap", "recover"):
        p = sub.add_parser(command)
        p.add_argument("--email", required=True)
        p.add_argument("--name", default="Superadmin")
    migrate = sub.add_parser("migrate-legacy")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument(
        "--email",
        action="append",
        default=[],
        metavar="USERNAME=EMAIL",
        help="Assign a real email to a legacy username (repeatable)",
    )
    args = parser.parse_args()
    try:
        if args.command == "migrate-legacy":
            mappings = _parse_email_mappings(args.email)
            _migrate_legacy(dry_run=args.dry_run, email_mappings=mappings)
            if not args.dry_run:
                print("Legacy users preserved with all_clients access; chat client IDs backfilled.")
            return 0
        password = _set_superadmin(args.email, args.name, require_empty=args.command == "bootstrap")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Temporary password (shown once):")
    print(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
