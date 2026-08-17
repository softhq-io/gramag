"""Persistent chat storage and specialist-memory retrieval for Proto."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from db_helpers import result_single, result_to_dicts, result_value
from embeddings import generate_embedding, generate_query_embedding
from proto.db_proto import proto_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_loads(value: str | None) -> Any:
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def _embed_message(text: str) -> list[float]:
    try:
        return generate_embedding(text)
    except Exception as e:
        print(f"chat embedding failed: {e}")
        return []


def create_session(
    *,
    machine_slug: str | None,
    customer: str | None,
    client_id: str | None,
    title: str | None,
    user: dict,
) -> dict:
    now = _now()
    session_id = _new_id("chat")
    clean_title = (title or "Neue Unterhaltung").strip()[:120]
    user_id = user["id"]
    proto_db.write(
        """
        CREATE (s:ProtoChatSession {
          id: $id,
          machine_slug: $machine_slug,
          customer: $customer,
          client_id: $client_id,
          title: $title,
          created_at: $now,
          updated_at: $now,
          created_by_id: $user_id,
          isolation_version: 3
        })
        """,
        {
            "id": session_id,
            "machine_slug": machine_slug,
            "customer": customer,
            "client_id": client_id,
            "title": clean_title,
            "now": now,
            "user_id": user_id,
        },
    )
    if machine_slug:
        proto_db.write(
            """
            MATCH (m:Machine {slug: $slug}), (s:ProtoChatSession {id: $id})
            MERGE (m)-[:HAS_CHAT]->(s)
            """,
            {"slug": machine_slug, "id": session_id},
        )
    return get_session(session_id, user=user) or {
        "id": session_id,
        "machine_slug": machine_slug,
        "customer": customer,
        "client_id": client_id,
        "title": clean_title,
        "created_at": now,
        "updated_at": now,
        "owned_by_me": True,
        "message_count": 0,
    }


def get_session(session_id: str, *, user: dict) -> dict | None:
    row = result_single(
        proto_db.query(
            """
            MATCH (s:ProtoChatSession {id: $id})
            OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(msg:ProtoChatMessage)
            WITH s, count(msg) AS message_count, max(msg.created_at) AS last_message_at
            RETURN s.id AS id,
                   s.machine_slug AS machine_slug,
                   s.customer AS customer,
                   s.title AS title,
                   s.created_at AS created_at,
                   s.updated_at AS updated_at,
                   s.created_by_id = $user_id AS owned_by_me,
                   message_count,
                   last_message_at
            """,
            {"id": session_id, "user_id": user["id"]},
        )
    )
    return row


def list_sessions(
    *,
    machine_slug: str | None,
    customer: str | None,
    user: dict,
    limit: int = 30,
) -> list[dict]:
    return result_to_dicts(
        proto_db.query(
            """
            MATCH (s:ProtoChatSession)
            OPTIONAL MATCH (m:Machine)
            WHERE m.slug = s.machine_slug
            WITH s, m
            WHERE ($machine_slug IS NULL OR s.machine_slug = $machine_slug)
              AND ($customer IS NULL OR coalesce(s.customer, '') = $customer)
              AND ($all_clients
                   OR (coalesce(s.isolation_version, 0) >= 2
                       AND s.created_by_id = $user_id
                       AND ((m.slug IS NOT NULL AND m.erp_customer_id IN $client_ids)
                            OR s.machine_slug IS NULL)))
            OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(msg:ProtoChatMessage)
            WITH s, count(msg) AS message_count, max(msg.created_at) AS last_message_at
            RETURN s.id AS id,
                   s.machine_slug AS machine_slug,
                   s.customer AS customer,
                   s.title AS title,
                   s.created_at AS created_at,
                   s.updated_at AS updated_at,
                   s.created_by_id = $user_id AS owned_by_me,
                   message_count,
                   last_message_at
            ORDER BY coalesce(last_message_at, s.updated_at) DESC
            LIMIT $limit
            """,
            {"machine_slug": machine_slug, "customer": customer, "limit": limit,
             "all_clients": bool(user.get("all_clients")),
             "client_ids": list(user.get("client_ids") or []), "user_id": user["id"]},
        )
    )


def _message_from_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "session_id": row.get("session_id"),
        "role": row.get("role"),
        "text": row.get("text") or "",
        "created_at": row.get("created_at"),
        "model": row.get("model"),
        "citations": _json_loads(row.get("citations_json")),
        "hits": _json_loads(row.get("hits_json")),
    }


def list_messages(session_id: str) -> list[dict]:
    rows = result_to_dicts(
        proto_db.query(
            """
            MATCH (:ProtoChatSession {id: $id})-[:HAS_MESSAGE]->(msg:ProtoChatMessage)
            RETURN msg.id AS id,
                   msg.session_id AS session_id,
                   msg.role AS role,
                   msg.text AS text,
                   msg.created_at AS created_at,
                   msg.model AS model,
                   msg.citations_json AS citations_json,
                   msg.hits_json AS hits_json
            ORDER BY msg.created_at ASC
            """,
            {"id": session_id},
        )
    )
    return [_message_from_row(row) for row in rows]


def append_message(
    *,
    session_id: str,
    role: str,
    text: str,
    model: str | None = None,
    citations: list[dict] | None = None,
    hits: list[dict] | None = None,
) -> dict:
    now = _now()
    message_id = _new_id("msg")
    params = {
        "session_id": session_id,
        "id": message_id,
        "role": role,
        "text": text,
        "created_at": now,
        "model": model,
        "citations_json": _json_dumps(citations),
        "hits_json": _json_dumps(hits),
    }
    proto_db.write(
        """
        MATCH (s:ProtoChatSession {id: $session_id})
        CREATE (msg:ProtoChatMessage {
          id: $id,
          session_id: $session_id,
          role: $role,
          text: $text,
          created_at: $created_at,
          model: $model,
          citations_json: $citations_json,
          hits_json: $hits_json
        })
        CREATE (s)-[:HAS_MESSAGE]->(msg)
        SET s.updated_at = $created_at
        """,
        params,
    )

    emb = _embed_message(f"{role}: {text}")
    if emb:
        proto_db.write(
            "MATCH (msg:ProtoChatMessage {id: $id}) SET msg.embedding = vecf32($emb)",
            {"id": message_id, "emb": emb},
        )

    return _message_from_row(params)


def retrieve_memory(
    *,
    query: str,
    session: dict,
    user: dict,
    limit: int = 6,
    min_score: float = 0.55,
) -> list[dict]:
    machine_slug = session.get("machine_slug")
    current_session_id = session.get("id")
    if not machine_slug:
        return []
    query_emb = generate_query_embedding(query)
    try:
        rows = result_to_dicts(
            proto_db.query(
                    """
                    CALL db.idx.vector.queryNodes(
                        'ProtoChatMessage', 'embedding', $k, vecf32($emb)
                    ) YIELD node AS msg, score
                    MATCH (s:ProtoChatSession)-[:HAS_MESSAGE]->(msg)
                    WHERE s.id <> $session_id
                      AND s.machine_slug = $machine_slug
                      AND coalesce(s.isolation_version, 0) >= 2
                      AND ($all_clients OR s.created_by_id = $user_id)
                    RETURN msg.id AS id,
                           msg.session_id AS session_id,
                           msg.role AS role,
                           msg.text AS text,
                           msg.created_at AS created_at,
                           msg.model AS model,
                           s.title AS session_title,
                           s.machine_slug AS machine_slug,
                           s.customer AS customer,
                           score
                    ORDER BY score DESC
                    LIMIT $k
                    """,
                    {
                        "emb": query_emb,
                        "k": limit,
                        "session_id": current_session_id,
                        "machine_slug": machine_slug,
                        "all_clients": bool(user.get("all_clients")),
                        "user_id": user["id"],
                    },
                )
            )
    except Exception as e:
        print(f"chat memory search failed (machine): {e}")
        return []
    return [row for row in rows if float(row.get("score") or 0) >= min_score][:limit]


def anonymize_chat_authors() -> dict[str, int]:
    """Remove stored display identities while preserving opaque ownership."""
    sessions = result_value(
        proto_db.write(
            """
            MATCH (s:ProtoChatSession)
            WHERE s.created_by IS NOT NULL
               OR (s.created_by_id IS NOT NULL AND coalesce(s.isolation_version, 0) < 3)
            WITH s, 1 AS changed
            REMOVE s.created_by
            SET s.isolation_version = CASE
                WHEN s.created_by_id IS NOT NULL THEN 3
                ELSE coalesce(s.isolation_version, 0)
            END
            RETURN sum(changed) AS changed
            """
        ),
        "changed",
        0,
    )
    messages = result_value(
        proto_db.write(
            """
            MATCH (msg:ProtoChatMessage)
            WHERE msg.username IS NOT NULL OR msg.user_role IS NOT NULL
            WITH msg, 1 AS changed
            REMOVE msg.username, msg.user_role
            RETURN sum(changed) AS changed
            """
        ),
        "changed",
        0,
    )
    return {"sessions": int(sessions or 0), "messages": int(messages or 0)}


def anonymize_deleted_user(user_id: str) -> None:
    """Make retained chats unlinkable from a permanently deleted user."""
    proto_db.write(
        """
        MATCH (:ProtoChatSession {created_by_id: $user_id})-[:HAS_MESSAGE]->(msg)
        REMOVE msg.username, msg.user_role
        """,
        {"user_id": user_id},
    )
    proto_db.write(
        """
        MATCH (s:ProtoChatSession {created_by_id: $user_id})
        REMOVE s.created_by, s.created_by_id
        SET s.isolation_version = 3
        """,
        {"user_id": user_id},
    )
