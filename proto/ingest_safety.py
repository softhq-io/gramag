"""Pre/post import checks that protect already-ingested Proto customer data."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from db_helpers import result_to_dicts, result_value
from proto.db_proto import proto_db


BASELINE_QUERIES = {
    "machines": """
        MATCH (c:Customer {name: $customer})-[:HAS_MACHINE]->(n:Machine)
        RETURN count(DISTINCT n) AS count
    """,
    "documents": """
        MATCH (c:Customer {name: $customer})-[:HAS_MACHINE]->(:Machine)
              -[:HAS_DOCUMENT]->(n:Document)
        RETURN count(DISTINCT n) AS count
    """,
    "categories": """
        MATCH (c:Customer {name: $customer})-[:HAS_MACHINE]->(:Machine)
              -[:HAS_CATEGORY]->(n:DocumentCategory)
        RETURN count(DISTINCT n) AS count
    """,
    "sections": """
        MATCH (c:Customer {name: $customer})-[:HAS_MACHINE]->(:Machine)
              -[:HAS_DOCUMENT]->(:Document)-[:HAS_SECTION]->(n:ManualSection)
        RETURN count(DISTINCT n) AS count
    """,
    "configs": """
        MATCH (c:Customer {name: $customer})-[:HAS_MACHINE]->(:Machine)
              -[:HAS_DOCUMENT]->(:Document)-[:HAS_CONFIG]->(n:ConfigFile)
        RETURN count(DISTINCT n) AS count
    """,
    "images": """
        MATCH (c:Customer {name: $customer})-[:HAS_MACHINE]->(:Machine)
              -[:HAS_DOCUMENT]->(:Document)-[:HAS_IMAGE]->(n:ImageAsset)
        RETURN count(DISTINCT n) AS count
    """,
}

PAYLOAD_RELATIONSHIPS = {
    "ManualSection": "HAS_SECTION",
    "ConfigFile": "HAS_CONFIG",
    "ImageAsset": "HAS_IMAGE",
}


def parse_protected_baseline(value: str | None) -> dict | None:
    if not value:
        return None
    baseline = json.loads(value)
    required = {"customer", *BASELINE_QUERIES}
    missing = sorted(required - set(baseline))
    if missing:
        raise ValueError(f"protected baseline is missing: {', '.join(missing)}")
    parsed = {"customer": str(baseline["customer"])}
    for key in BASELINE_QUERIES:
        parsed[key] = int(baseline[key])
    return parsed


def protected_customer_counts(customer: str, db=proto_db) -> dict:
    return {
        key: int(result_value(db.query(query, {"customer": customer}), "count", 0))
        for key, query in BASELINE_QUERIES.items()
    }


def assert_protected_customer_baseline(baseline: dict, db=proto_db) -> dict:
    customer = baseline["customer"]
    actual = protected_customer_counts(customer, db=db)
    expected = {key: baseline[key] for key in BASELINE_QUERIES}
    if actual != expected:
        differences = ", ".join(
            f"{key}: expected {expected[key]}, found {actual[key]}"
            for key in BASELINE_QUERIES
            if actual[key] != expected[key]
        )
        raise RuntimeError(
            f"protected customer baseline changed for {customer}: {differences}"
        )
    return actual


def _record_identity(record: dict) -> tuple[str, str, str]:
    machine = record["machine"]
    return record["doc_id"], machine["slug"], machine.get("customer") or ""


def read_staged_identities(output_dir: Path) -> dict:
    documents: dict[str, tuple[str, str]] = {}
    machines: dict[str, str] = {}
    payloads: dict[str, dict[str, str]] = {
        label: {} for label in PAYLOAD_RELATIONSHIPS
    }
    record_count = 0

    for path in sorted(output_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as source:
            for line_no, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                record_count += 1
                record = json.loads(line)
                doc_id, machine_slug, customer = _record_identity(record)
                expected_document = (machine_slug, customer)
                previous_document = documents.setdefault(doc_id, expected_document)
                if previous_document != expected_document:
                    raise RuntimeError(
                        f"staged document ID {doc_id} has conflicting owners in "
                        f"{path.name}:{line_no}"
                    )
                previous_customer = machines.setdefault(machine_slug, customer)
                if previous_customer != customer:
                    raise RuntimeError(
                        f"staged machine slug {machine_slug} has conflicting customers"
                    )

                record_type = record.get("record")
                if record_type == "manual_section":
                    label, node_id = "ManualSection", record["section"]["id"]
                elif record_type == "config":
                    label, node_id = "ConfigFile", record["config"]["id"]
                elif record_type == "image":
                    label, node_id = "ImageAsset", record["image"]["id"]
                else:
                    continue
                previous_doc_id = payloads[label].setdefault(node_id, doc_id)
                if previous_doc_id != doc_id:
                    raise RuntimeError(
                        f"staged {label} ID {node_id} belongs to multiple documents"
                    )

    if not record_count:
        raise RuntimeError(f"no staged JSONL records found in {output_dir}")
    return {
        "documents": documents,
        "machines": machines,
        "payloads": payloads,
        "record_count": record_count,
    }


def _chunks(values: list[str], size: int = 500):
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _existing_rows(db, query: str, ids: list[str]) -> list[dict]:
    rows = []
    for batch in _chunks(ids):
        rows.extend(result_to_dicts(db.query(query, {"ids": batch})))
    return rows


def assert_staged_ids_are_safe(output_dir: Path, protected_customer: str, db=proto_db) -> dict:
    identities = read_staged_identities(output_dir)
    staged_customers = set(identities["machines"].values())
    if protected_customer in staged_customers:
        raise RuntimeError(
            f"staged import contains protected customer {protected_customer}"
        )

    machine_slugs = list(identities["machines"])
    if machine_slugs:
        rows = _existing_rows(
            db,
            """
            MATCH (m:Machine)
            WHERE m.slug IN $ids
            RETURN m.slug AS machine_slug, m.customer AS customer
            """,
            machine_slugs,
        )
        conflicts = [
            row for row in rows
            if (row.get("customer") or "")
            != identities["machines"].get(row["machine_slug"])
        ]
        if conflicts:
            raise RuntimeError(
                f"staged machine identity collision: {conflicts[:5]}"
            )

    doc_ids = list(identities["documents"])
    if doc_ids:
        rows = _existing_rows(
            db,
            """
            MATCH (d:Document)
            WHERE d.id IN $ids
            OPTIONAL MATCH (m:Machine)-[:HAS_DOCUMENT]->(d)
            RETURN d.id AS doc_id, m.slug AS machine_slug, m.customer AS customer
            """,
            doc_ids,
        )
        conflicts = []
        for row in rows:
            expected = identities["documents"].get(row["doc_id"])
            actual = (row.get("machine_slug"), row.get("customer") or "")
            if actual != expected:
                conflicts.append(row)
        if conflicts:
            raise RuntimeError(
                f"staged document identity collision: {conflicts[:5]}"
            )

    for label, relation in PAYLOAD_RELATIONSHIPS.items():
        expected_owners = identities["payloads"][label]
        node_ids = list(expected_owners)
        if not node_ids:
            continue
        rows = _existing_rows(
            db,
            f"""
            MATCH (n:{label})
            WHERE n.id IN $ids
            OPTIONAL MATCH (d:Document)-[:{relation}]->(n)
            RETURN n.id AS node_id, d.id AS doc_id
            """,
            node_ids,
        )
        conflicts = [
            row for row in rows
            if row.get("doc_id") != expected_owners.get(row["node_id"])
        ]
        if conflicts:
            raise RuntimeError(
                f"staged {label} identity collision: {conflicts[:5]}"
            )

    return identities


def verify_pre_import(output_dir: Path, baseline: dict, db=proto_db) -> dict:
    counts = assert_protected_customer_baseline(baseline, db=db)
    identities = assert_staged_ids_are_safe(
        output_dir,
        baseline["customer"],
        db=db,
    )
    print(
        "Import safety verified: "
        f"protected_customer={baseline['customer']!r} "
        f"protected_counts={counts} "
        f"staged_records={identities['record_count']} "
        f"staged_documents={len(identities['documents'])}",
        flush=True,
    )
    return identities

