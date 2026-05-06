"""Import a small new-ERP subset JSON into the local graph.

Input format: output of demo_subset_new_erp.py.

This script is idempotent (MERGE-based) and updates only selected machines/docs.
It does NOT delete existing data.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from config import NOISE_KEYWORDS
from db import db


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import new ERP subset JSON into FalkorDB.")
    p.add_argument("--input", required=True, help="Path to demo subset JSON file.")
    return p.parse_args()


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def is_noise_part(title: str | None) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in NOISE_KEYWORDS)


def upsert_customer(customer_id: str | None, customer_nummer: str | None):
    if not customer_id:
        return
    name = f"Kunde {customer_nummer}" if customer_nummer else f"Kunde {customer_id}"
    db.write(
        """
        MERGE (c:Customer {erp_id: $erp_id})
        SET c.nummer = coalesce($nummer, c.nummer),
            c.name = coalesce(c.name, $name),
            c.source_new_erp = true,
            c.new_erp_sync_ts = $sync_ts
        """,
        {
            "erp_id": str(customer_id),
            "nummer": customer_nummer,
            "name": name,
            "sync_ts": datetime.now(timezone.utc).isoformat(),
        },
    )


def upsert_machine(machine: dict[str, Any]):
    machine_id = str(machine.get("id") or "").strip()
    if not machine_id:
        return
    db.write(
        """
        MERGE (m:Machine {erp_id: $erp_id})
        SET m.title = coalesce($title, m.title),
            m.serial_number = coalesce($serial, m.serial_number),
            m.new_erp_nummer = coalesce($nummer, m.new_erp_nummer),
            m.new_erp_create_date = coalesce($create_date, m.new_erp_create_date),
            m.new_erp_edit_date = coalesce($edit_date, m.new_erp_edit_date),
            m.source_new_erp = true,
            m.new_erp_sync_ts = $sync_ts
        """,
        {
            "erp_id": machine_id,
            "title": machine.get("titel"),
            "serial": machine.get("seriennummer"),
            "nummer": machine.get("nummer"),
            "create_date": machine.get("createDate"),
            "edit_date": machine.get("editDate"),
            "sync_ts": datetime.now(timezone.utc).isoformat(),
        },
    )
    ref_kunde = machine.get("refKunde") or {}
    customer_id = str(ref_kunde.get("id") or "").strip() if ref_kunde else ""
    if customer_id:
        upsert_customer(customer_id, ref_kunde.get("nummer"))
        db.write(
            """
            MATCH (c:Customer {erp_id: $customer_id})
            MATCH (m:Machine {erp_id: $machine_id})
            MERGE (c)-[:OWNS]->(m)
            """,
            {"customer_id": customer_id, "machine_id": machine_id},
        )


def upsert_service_job(machine_id: str, service_doc: dict[str, Any]):
    doc_id = str(service_doc.get("id") or "").strip()
    if not doc_id:
        return
    db.write(
        """
        MERGE (sj:ServiceJob {erp_id: $erp_id})
        SET sj.nummer = coalesce($nummer, sj.nummer),
            sj.title = coalesce($title, sj.title),
            sj.description = coalesce($description, sj.description),
            sj.date = coalesce($date, sj.date),
            sj.new_erp_create_date = coalesce($create_date, sj.new_erp_create_date),
            sj.new_erp_edit_date = coalesce($edit_date, sj.new_erp_edit_date),
            sj.source_new_erp = true,
            sj.new_erp_sync_ts = $sync_ts
        """,
        {
            "erp_id": doc_id,
            "nummer": service_doc.get("nummer"),
            "title": service_doc.get("bezeichnung"),
            "description": service_doc.get("bezeichnung"),
            "date": service_doc.get("dokDatum"),
            "create_date": service_doc.get("createDate"),
            "edit_date": service_doc.get("editDate"),
            "sync_ts": datetime.now(timezone.utc).isoformat(),
        },
    )
    db.write(
        """
        MATCH (sj:ServiceJob {erp_id: $doc_id})
        MATCH (m:Machine {erp_id: $machine_id})
        MERGE (sj)-[:FOR_MACHINE]->(m)
        """,
        {"doc_id": doc_id, "machine_id": machine_id},
    )

    ref_kunde = service_doc.get("refKunde") or {}
    customer_id = str(ref_kunde.get("id") or "").strip() if ref_kunde else ""
    if customer_id:
        upsert_customer(customer_id, ref_kunde.get("nummer"))
        db.write(
            """
            MATCH (sj:ServiceJob {erp_id: $doc_id})
            MATCH (c:Customer {erp_id: $customer_id})
            MERGE (sj)-[:FOR_CUSTOMER]->(c)
            """,
            {"doc_id": doc_id, "customer_id": customer_id},
        )


def upsert_comment(doc_id: str, comment: dict[str, Any]):
    cid = str(comment.get("id") or "").strip()
    if not cid:
        return
    text = (comment.get("kommentar") or "").strip()
    db.write(
        """
        MERGE (sc:ServiceComment {erp_id: $erp_id})
        SET sc.text = coalesce($text, sc.text),
            sc.date = coalesce($date, sc.date),
            sc.new_erp_ref_user = coalesce($ref_user, sc.new_erp_ref_user),
            sc.source_new_erp = true,
            sc.new_erp_sync_ts = $sync_ts
        WITH sc
        MATCH (sj:ServiceJob {erp_id: $doc_id})
        MERGE (sc)-[:ON_JOB]->(sj)
        """,
        {
            "erp_id": cid,
            "text": text[:2000] if text else "",
            "date": comment.get("datum"),
            "ref_user": comment.get("refUser"),
            "sync_ts": datetime.now(timezone.utc).isoformat(),
            "doc_id": doc_id,
        },
    )


def upsert_part_and_edge(doc_id: str, part_row: dict[str, Any]):
    ref_art = part_row.get("refArt") or {}
    part_erp_id = str(ref_art.get("id") or "").strip()
    part_nummer = (ref_art.get("nummer") or part_row.get("nummer") or "").strip()
    part_title = (ref_art.get("lang1titel") or part_row.get("titel") or "").strip()
    part_mfr = (ref_art.get("herstellernr") or "").strip()
    if not part_erp_id and not part_nummer:
        return

    noise = is_noise_part(part_title)

    if part_erp_id:
        db.write(
            """
            MERGE (p:Part {erp_id: $erp_id})
            SET p.nummer = coalesce($nummer, p.nummer),
                p.titel = coalesce($titel, p.titel),
                p.manufacturer_nr = coalesce($manufacturer_nr, p.manufacturer_nr),
                p.noise = coalesce(p.noise, $noise),
                p.source_new_erp = true,
                p.new_erp_sync_ts = $sync_ts
            """,
            {
                "erp_id": part_erp_id,
                "nummer": part_nummer or None,
                "titel": part_title or None,
                "manufacturer_nr": part_mfr or None,
                "noise": noise,
                "sync_ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        edge_match = "MATCH (p:Part {erp_id: $part_id})"
        edge_params = {"part_id": part_erp_id}
    else:
        db.write(
            """
            MERGE (p:Part {nummer: $nummer})
            SET p.titel = coalesce($titel, p.titel),
                p.manufacturer_nr = coalesce($manufacturer_nr, p.manufacturer_nr),
                p.noise = coalesce(p.noise, $noise),
                p.source_new_erp = true,
                p.new_erp_sync_ts = $sync_ts
            """,
            {
                "nummer": part_nummer,
                "titel": part_title or None,
                "manufacturer_nr": part_mfr or None,
                "noise": noise,
                "sync_ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        edge_match = "MATCH (p:Part {nummer: $part_nummer})"
        edge_params = {"part_nummer": part_nummer}

    db.write(
        f"""
        MATCH (sj:ServiceJob {{erp_id: $doc_id}})
        {edge_match}
        MERGE (sj)-[u:USED_PART]->(p)
        SET u.quantity = coalesce($quantity, u.quantity),
            u.price = coalesce($price, u.price),
            u.source_new_erp = true,
            u.new_erp_sync_ts = $sync_ts
        """,
        {
            "doc_id": doc_id,
            "quantity": to_float(part_row.get("anzahl")),
            "price": to_float(part_row.get("preis")),
            "sync_ts": datetime.now(timezone.utc).isoformat(),
            **edge_params,
        },
    )


def main() -> int:
    args = parse_args()
    with open(args.input, "r", encoding="utf-8") as f:
        payload = json.load(f)

    machines = payload.get("machines") or []
    db.connect()

    count_machines = 0
    count_docs = 0
    count_comments = 0
    count_parts = 0

    for m in machines:
        machine = m.get("machine") or {}
        machine_id = str(machine.get("id") or m.get("machine_id") or "").strip()
        if not machine_id or m.get("error"):
            continue

        upsert_machine(machine)
        count_machines += 1

        for sd in m.get("service_documents") or []:
            doc = sd.get("service_document") or {}
            doc_id = str(doc.get("id") or "").strip()
            if not doc_id:
                continue

            upsert_service_job(machine_id, doc)
            count_docs += 1

            for c in sd.get("comments") or []:
                upsert_comment(doc_id, c)
                count_comments += 1

            for p in sd.get("parts") or []:
                upsert_part_and_edge(doc_id, p)
                count_parts += 1

    report = {
        "machines_processed": count_machines,
        "service_docs_processed": count_docs,
        "comments_processed": count_comments,
        "parts_rows_processed": count_parts,
        "source_file": args.input,
    }
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
