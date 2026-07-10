"""Compact ERP context for Proto answers."""

from __future__ import annotations

import html
import json
import os
import re

from db import db
from db_helpers import result_single, result_to_dicts
from proto.db_proto import proto_db

DEFAULT_MIN_CONFIDENCE = float(os.getenv("PROTO_ERP_CONTEXT_MIN_CONFIDENCE", "0.78"))


def _clean_text(value: str | None, limit: int = 240) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit].rsplit(" ", 1)[0] + "..."
    return text


def get_proto_erp_link(machine_slug: str | None, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> dict | None:
    if not machine_slug:
        return None
    row = result_single(
        proto_db.query(
            """
            MATCH (m:Machine {slug: $slug})
            RETURN m.slug AS machine_slug,
                   m.folder AS proto_folder,
                   m.erp_id AS erp_id,
                   m.erp_customer_id AS erp_customer_id,
                   m.erp_link_mode AS erp_link_mode,
                   m.erp_related_ids AS erp_related_ids,
                   m.erp_related_ids_json AS erp_related_ids_json,
                   m.erp_group_identifier AS erp_group_identifier,
                   m.erp_match_method AS erp_match_method,
                   m.erp_match_confidence AS erp_match_confidence
            """,
            {"slug": machine_slug},
        )
    )
    if not row or not row.get("erp_id"):
        if not row or not _erp_ids_from_link(row):
            return None
    if not row.get("erp_id") and row.get("erp_link_mode") != "group":
        return None
    confidence = float(row.get("erp_match_confidence") or 0)
    if confidence < min_confidence:
        return None
    return row


def _erp_ids_from_link(link: dict) -> list[str]:
    ids = link.get("erp_related_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if not ids and link.get("erp_related_ids_json"):
        try:
            ids = json.loads(link["erp_related_ids_json"])
        except Exception:
            ids = []
    if not ids and link.get("erp_id"):
        ids = [link["erp_id"]]
    return [str(item) for item in ids if item]


def _preview(values: list[str], limit: int = 6) -> str:
    shown = [value for value in values if value][:limit]
    suffix = f" (+{len(values) - limit} more)" if len(values) > limit else ""
    return ", ".join(shown) + suffix if shown else "-"


def retrieve_erp_context(
    machine_slug: str | None,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    job_limit: int = 5,
    part_limit: int = 8,
) -> dict | None:
    link = get_proto_erp_link(machine_slug, min_confidence=min_confidence)
    if not link:
        return None
    erp_ids = _erp_ids_from_link(link)
    if not erp_ids:
        return None

    machines = result_to_dicts(
        db.query(
            """
            MATCH (m:Machine)
            WHERE m.erp_id IN $erp_ids
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            OPTIONAL MATCH (m)-[:IS_TYPE]->(mt:MachineType)
            OPTIONAL MATCH (m)-[:MADE_BY]->(mb:MachineBrand)
            RETURN m.erp_id AS erp_id,
                   m.title AS title,
                   m.serial_number AS serial_number,
                   m.new_erp_nummer AS nummer,
                   c.erp_id AS customer_erp_id,
                   c.name AS customer,
                   mt.name AS machine_type,
                   mb.name AS brand
            ORDER BY m.erp_id
            """,
            {"erp_ids": erp_ids},
        )
    )
    if not machines:
        return None

    jobs = result_to_dicts(
        db.query(
            """
            MATCH (m:Machine)<-[:FOR_MACHINE]-(sj:ServiceJob)
            WHERE m.erp_id IN $erp_ids
            OPTIONAL MATCH (sc:ServiceComment)-[:ON_JOB]->(sj)
            WITH m, sj, collect(DISTINCT {
                text: sc.text,
                date: sc.date,
                author: sc.author
            })[0..3] AS comments
            ORDER BY sj.date DESC
            LIMIT $limit
            RETURN sj.erp_id AS erp_id,
                   sj.title AS title,
                   sj.nummer AS nummer,
                   sj.date AS date,
                   sj.description AS description,
                   m.erp_id AS machine_erp_id,
                   m.title AS machine_title,
                   comments
            """,
            {"erp_ids": erp_ids, "limit": job_limit},
        )
    )

    parts = result_to_dicts(
        db.query(
            """
            MATCH (m:Machine)<-[:FOR_MACHINE]-(sj:ServiceJob)
            WHERE m.erp_id IN $erp_ids
            MATCH (sj)-[:USED_PART]->(p:Part)
            WHERE NOT coalesce(p.noise, false)
            RETURN p.nummer AS nummer,
                   p.titel AS titel,
                   p.manufacturer_nr AS manufacturer_nr,
                   count(DISTINCT sj) AS usage_count
            ORDER BY usage_count DESC, p.nummer
            LIMIT $limit
            """,
            {"erp_ids": erp_ids, "limit": part_limit},
        )
    )

    last_service_date = jobs[0].get("date") if jobs else None
    for job in jobs:
        job["description"] = _clean_text(job.get("description"), limit=180)
        cleaned_comments = []
        for comment in job.get("comments") or []:
            text = _clean_text((comment or {}).get("text"), limit=180)
            if text:
                cleaned_comments.append({
                    "text": text,
                    "date": (comment or {}).get("date"),
                    "author": (comment or {}).get("author"),
                })
        job["comments"] = cleaned_comments[:3]

    return {
        "link": link,
        "machine": machines[0],
        "machines": machines,
        "erp_ids": erp_ids,
        "recent_jobs": jobs,
        "frequent_parts": parts,
        "last_service_date": last_service_date,
    }


def format_erp_context(context: dict | None) -> str:
    if not context:
        return "(no linked ERP context available)"

    machine = context.get("machine") or {}
    machines = context.get("machines") or ([machine] if machine else [])
    link = context.get("link") or {}
    is_group = (link.get("erp_link_mode") == "group") or len(context.get("erp_ids") or []) > 1
    if is_group:
        erp_ids = context.get("erp_ids") or []
        titles = []
        for item in machines:
            title = item.get("title")
            if title and title not in titles:
                titles.append(title)
        lines = [
            "ERP RELATED MACHINE/LINE RECORDS:",
            f"- Group identifier: {link.get('erp_group_identifier') or '-'}",
            f"- ERP record count: {len(erp_ids)}",
            f"- Representative ERP IDs: {_preview(erp_ids)}",
            f"- Representative titles: {_preview(titles, limit=5)}",
            f"- Link confidence: {link.get('erp_match_confidence')} ({link.get('erp_match_method')})",
            f"- Last service date: {context.get('last_service_date') or '-'}",
            "- Answer guidance: treat these records as one related machine/line group; do not list every ERP ID unless asked.",
        ]
    else:
        lines = [
            "ERP MACHINE RECORD:",
            f"- ERP ID: {machine.get('erp_id') or '?'}",
            f"- Title: {machine.get('title') or '?'}",
            f"- Serial: {machine.get('serial_number') or '-'}",
            f"- Customer: {machine.get('customer') or '-'}",
            f"- Type/brand: {machine.get('machine_type') or '-'} / {machine.get('brand') or '-'}",
            f"- Link confidence: {link.get('erp_match_confidence')} ({link.get('erp_match_method')})",
            f"- Last service date: {context.get('last_service_date') or '-'}",
        ]
    lines.extend(["", "ERP FREQUENT PART USAGE:"])
    parts = context.get("frequent_parts") or []
    if parts:
        for part in parts:
            lines.append(
                f"- {part.get('nummer') or '?'}: {part.get('titel') or '-'} "
                f"(used {part.get('usage_count') or 0}x)"
            )
    else:
        lines.append("- none found")

    lines.extend(["", "ERP RECENT SERVICE HISTORY:"])
    jobs = context.get("recent_jobs") or []
    if jobs:
        for job in jobs:
            lines.append(
                f"- [{job.get('date') or '?'}] {job.get('nummer') or job.get('erp_id') or '?'}"
                f" @ {job.get('machine_erp_id') or '?'}: "
                f"{job.get('title') or job.get('description') or '-'}"
            )
            for comment in (job.get("comments") or [])[:2]:
                lines.append(f"  Comment: {comment.get('text')}")
    else:
        lines.append("- none found")
    return "\n".join(lines)
