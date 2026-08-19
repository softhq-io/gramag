"""Adopt legacy SharePoint Proto content into managed knowledge metadata.

The default is a read-only report. Pass ``--apply`` after taking graph and
app-data snapshots. The migration is idempotent and preserves machine slugs,
document IDs, derived nodes, and chat references.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from db import db
from db_helpers import result_single, result_to_dicts
from proto.db_proto import proto_db


def build_report() -> dict:
    machines = result_to_dicts(
        proto_db.query(
            """
            MATCH (m:Machine)
            OPTIONAL MATCH (old:Customer)-[:HAS_MACHINE]->(m)
            OPTIONAL MATCH (m)-[:HAS_DOCUMENT]->(d:Document)
            RETURN m.slug AS slug, m.erp_id AS erp_id,
                   m.erp_customer_id AS client_id, m.erp_link_mode AS link_mode,
                   coalesce(old.name, m.customer) AS customer,
                   count(DISTINCT d) AS documents
            ORDER BY customer, slug
            """
        )
    )
    linked = [row for row in machines if row.get("client_id")]
    quarantined = [row for row in machines if not row.get("client_id")]
    return {
        "machine_count": len(machines),
        "linked_machine_count": len(linked),
        "quarantined_machine_count": len(quarantined),
        "document_count": sum(int(row.get("documents") or 0) for row in machines),
        "clients_to_activate": sorted({row["client_id"] for row in linked}),
        "quarantined": quarantined,
    }


def apply_migration() -> None:
    proto_db.write(
        """
        MATCH (old:Customer)-[r:HAS_MACHINE]->(m:Machine)
        WHERE m.erp_customer_id IS NOT NULL
        MERGE (c:Customer {erp_id: m.erp_customer_id})
        ON CREATE SET c.id = 'erp:' + m.erp_customer_id,
                      c.name = coalesce(old.name, m.customer)
        SET c.name = coalesce(c.name, old.name, m.customer), c.active = true,
            m.selected = true, m.quarantined = false
        MERGE (c)-[:HAS_MACHINE]->(m)
        """
    )
    proto_db.write(
        """
        MATCH (old:Customer)-[r:HAS_MACHINE]->(m:Machine)
        WHERE m.erp_customer_id IS NOT NULL
          AND coalesce(old.erp_id, '') <> m.erp_customer_id
        DELETE r
        """
    )
    proto_db.write(
        """
        MATCH (m:Machine)
        WHERE m.erp_customer_id IS NULL
        SET m.selected = false, m.quarantined = true
        """
    )
    documents = result_to_dicts(
        proto_db.query(
            """
            MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document)
            RETURN d.id AS id, d.path AS path, d.rel_path AS rel_path,
                   d.status AS status, d.source AS source
            """
        )
    )
    for document in documents:
        raw_path = document.get("path") or ""
        try:
            storage_rel = str(Path(raw_path).relative_to("/data")) if raw_path else document.get("rel_path")
        except ValueError:
            storage_rel = document.get("rel_path") or raw_path
        proto_db.write(
            """
            MATCH (d:Document {id: $id})
            SET d.status = coalesce(d.status, 'ready'),
                d.phase = coalesce(d.phase, 'ready'),
                d.source = coalesce(d.source, 'sharepoint_legacy'),
                d.original_name = coalesce(d.original_name, d.name),
                d.storage_rel_path = coalesce(d.storage_rel_path, $storage_rel),
                d.progress_current = coalesce(d.progress_current, 0),
                d.progress_total = coalesce(d.progress_total, 0),
                d.retry_count = coalesce(d.retry_count, 0)
            """,
            {"id": document["id"], "storage_rel": storage_rel},
        )
    proto_db.write(
        """
        MATCH (c:Customer)
        WHERE c.erp_id IS NULL AND NOT (c)-[:HAS_MACHINE]->(:Machine)
        DETACH DELETE c
        """
    )


def load_mappings(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Mapping file must be an object keyed by Proto machine slug")
    mappings: dict[str, dict] = {}
    for slug, value in raw.items():
        entry = {"client_id": value} if isinstance(value, str) else value
        if not isinstance(entry, dict) or not str(entry.get("client_id") or "").strip():
            raise ValueError(f"Mapping for {slug!r} must provide client_id")
        mappings[str(slug)] = {
            "client_id": str(entry["client_id"]).strip(),
            "erp_machine_id": str(entry.get("erp_machine_id") or "").strip() or None,
        }
    return mappings


def apply_mappings(mappings: dict[str, dict]) -> None:
    for slug, mapping in mappings.items():
        client_id = mapping["client_id"]
        machine_id = mapping.get("erp_machine_id")
        if machine_id:
            crm = result_single(
                db.query(
                    """
                    MATCH (c:Customer {erp_id: $client_id})-[:OWNS]->(m:Machine {erp_id: $machine_id})
                    RETURN c.erp_id AS client_id, c.name AS client_name, m.erp_id AS machine_id
                    """,
                    {"client_id": client_id, "machine_id": machine_id},
                )
            )
        else:
            crm = result_single(
                db.query(
                    """
                    MATCH (c:Customer {erp_id: $client_id})
                    RETURN c.erp_id AS client_id, c.name AS client_name
                    """,
                    {"client_id": client_id},
                )
            )
        if not crm:
            raise ValueError(f"Mapping for {slug!r} does not resolve to the requested CRM ownership")
        matched = result_single(
            proto_db.write(
                """
                MATCH (m:Machine {slug: $slug})
                MERGE (c:Customer {erp_id: $client_id})
                ON CREATE SET c.id = 'erp:' + $client_id
                SET c.name = $client_name, c.active = true
                SET m.erp_customer_id = $client_id,
                    m.erp_id = coalesce($machine_id, m.erp_id),
                    m.erp_link_mode = 'manual', m.quarantined = false
                MERGE (c)-[:HAS_MACHINE]->(m)
                RETURN m.slug AS slug
                """,
                {
                    "slug": slug,
                    "client_id": client_id,
                    "client_name": crm.get("client_name") or client_id,
                    "machine_id": machine_id,
                },
            )
        )
        if not matched:
            raise ValueError(f"Proto machine slug {slug!r} was not found")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the migration; default is dry-run")
    parser.add_argument(
        "--mapping-file",
        type=Path,
        help="Optional JSON mapping of quarantined machine slugs to CRM client/machine IDs",
    )
    args = parser.parse_args()
    proto_db.connect()
    mappings = load_mappings(args.mapping_file)
    before = build_report()
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "mapping_count": len(mappings),
        "before": before,
    }, indent=2))
    if not args.apply:
        return
    if mappings:
        db.connect()
        apply_mappings(mappings)
    apply_migration()
    after = build_report()
    print(json.dumps({"mode": "complete", "after": after}, indent=2))


if __name__ == "__main__":
    main()
