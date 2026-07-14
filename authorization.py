"""Fail-closed resource authorization across ERP and Proto graphs."""

from __future__ import annotations

from fastapi import HTTPException

from auth import scope_params
from db import db
from db_helpers import result_single
from proto.db_proto import proto_db


def not_found(label: str = "Resource") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{label} not found")


def require_erp_machine(user: dict, erp_id: str) -> dict:
    row = result_single(
        db.query(
            """
            MATCH (m:Machine {erp_id: $erp_id})
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            WITH m, c
            WHERE $all_clients OR c.erp_id IN $client_ids
            RETURN m.erp_id AS erp_id, m.title AS title,
                   c.erp_id AS client_id, c.name AS client_name
            """,
            scope_params(user, erp_id=erp_id),
        )
    )
    if not row:
        raise not_found("Machine")
    return row


def require_proto_machine(user: dict, machine_slug: str) -> dict:
    row = result_single(
        proto_db.query(
            """
            MATCH (m:Machine {slug: $slug})
            WHERE $all_clients OR m.erp_customer_id IN $client_ids
            RETURN m.slug AS slug, m.folder AS folder,
                   m.customer AS customer, m.erp_customer_id AS client_id,
                   m.erp_id AS erp_id
            """,
            scope_params(user, slug=machine_slug),
        )
    )
    if not row:
        raise not_found("Machine")
    return row


def require_proto_section(user: dict, section_id: str) -> dict:
    row = result_single(
        proto_db.query(
            """
            MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document)-[:HAS_SECTION]->(s:ManualSection {id: $id})
            WHERE $all_clients OR m.erp_customer_id IN $client_ids
            RETURN s.id AS id, s.page AS page, s.text AS text,
                   s.vision_desc AS vision_desc, s.merged AS merged,
                   s.png_path AS png_path, d.name AS doc_name, d.id AS doc_id,
                   m.folder AS machine, m.slug AS machine_slug
            """,
            scope_params(user, id=section_id),
        )
    )
    if not row:
        raise not_found("Section")
    return row


def require_proto_document(user: dict, document_id: str) -> dict:
    row = result_single(
        proto_db.query(
            """
            MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document {id: $id})
            WHERE $all_clients OR m.erp_customer_id IN $client_ids
            RETURN d.id AS id, d.path AS path, d.name AS name, d.kind AS kind,
                   m.slug AS machine_slug, m.erp_customer_id AS client_id
            LIMIT 1
            """,
            scope_params(user, id=document_id),
        )
    )
    if not row:
        raise not_found("Document")
    return row


def require_proto_asset(user: dict, asset_id: str) -> dict:
    row = result_single(
        proto_db.query(
            """
            MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document)-[:HAS_IMAGE]->(i:ImageAsset {id: $id})
            WHERE $all_clients OR m.erp_customer_id IN $client_ids
            RETURN i.id AS id, i.path AS path, i.name AS name,
                   m.slug AS machine_slug, m.erp_customer_id AS client_id
            """,
            scope_params(user, id=asset_id),
        )
    )
    if not row:
        raise not_found("Asset")
    return row


def require_proto_chat(user: dict, chat_id: str) -> dict:
    row = result_single(
        proto_db.query(
            """
            MATCH (s:ProtoChatSession {id: $id})
            OPTIONAL MATCH (m:Machine)
            WHERE m.slug = s.machine_slug
            WITH s, m
            WHERE $all_clients
               OR (coalesce(s.isolation_version, 0) >= 2 AND
                   ((m.slug IS NOT NULL AND m.erp_customer_id IN $client_ids)
                    OR (m.slug IS NULL AND s.created_by_id = $user_id)))
            OPTIONAL MATCH (s)-[:HAS_MESSAGE]->(msg:ProtoChatMessage)
            WITH s, count(msg) AS message_count, max(msg.created_at) AS last_message_at
            RETURN s.id AS id, s.machine_slug AS machine_slug,
                   s.client_id AS client_id, s.customer AS customer,
                   s.title AS title, s.created_at AS created_at,
                   s.updated_at AS updated_at, s.created_by AS created_by,
                   s.created_by_id AS created_by_id,
                   coalesce(s.isolation_version, 0) AS isolation_version,
                   message_count, last_message_at
            """,
            scope_params(user, id=chat_id, user_id=user["id"]),
        )
    )
    if not row:
        raise not_found("Chat")
    return row
