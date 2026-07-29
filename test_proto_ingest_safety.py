from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from proto import ingest_safety


class FakeResult:
    def __init__(self, rows: list[dict]):
        keys = list(rows[0]) if rows else []
        self.header = [[1, key] for key in keys]
        self.result_set = [[row[key] for key in keys] for row in rows]


class FakeDb:
    def __init__(self, counts: dict, existing: dict | None = None):
        self.counts = counts
        self.existing = existing or {}

    def query(self, query, params):
        if "RETURN count(DISTINCT n) AS count" in query:
            key = next(key for key in ingest_safety.BASELINE_QUERIES if query == ingest_safety.BASELINE_QUERIES[key])
            return FakeResult([{"count": self.counts[key]}])
        if "MATCH (d:Document)" in query:
            return FakeResult([
                row for row in self.existing.get("documents", [])
                if row["doc_id"] in params["ids"]
            ])
        if "MATCH (m:Machine)" in query:
            return FakeResult([
                row for row in self.existing.get("machines", [])
                if row["machine_slug"] in params["ids"]
            ])
        for label in ingest_safety.PAYLOAD_RELATIONSHIPS:
            if f"MATCH (n:{label})" in query:
                return FakeResult([
                    row for row in self.existing.get(label, [])
                    if row["node_id"] in params["ids"]
                ])
        raise AssertionError(query)


def write_stage(path: Path, *, customer: str = "New Customer"):
    machine = {
        "slug": "new-customer-machine-a",
        "customer": customer,
        "folder": "Machine A",
    }
    records = [
        {
            "machine": machine,
            "doc_id": "doc-1",
            "record": "document",
        },
        {
            "machine": machine,
            "doc_id": "doc-1",
            "record": "manual_section",
            "section": {"id": "section-1"},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


class ProtoIngestSafetyTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {
            "customer": "Birkhäuser + GBC AG",
            "machines": 13,
            "documents": 4980,
            "categories": 59,
            "sections": 28609,
            "configs": 1250,
            "images": 3218,
        }

    def test_parse_protected_baseline_requires_all_counts(self):
        with self.assertRaises(ValueError):
            ingest_safety.parse_protected_baseline('{"customer":"Birkhäuser"}')

    def test_verify_pre_import_accepts_new_ids_and_exact_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            write_stage(output_dir / "machine.jsonl")
            identities = ingest_safety.verify_pre_import(
                output_dir,
                self.baseline,
                db=FakeDb({key: self.baseline[key] for key in ingest_safety.BASELINE_QUERIES}),
            )
        self.assertEqual(identities["documents"]["doc-1"], ("new-customer-machine-a", "New Customer"))

    def test_verify_pre_import_rejects_protected_customer_regression(self):
        counts = {key: self.baseline[key] for key in ingest_safety.BASELINE_QUERIES}
        counts["documents"] -= 1
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            write_stage(output_dir / "machine.jsonl")
            with self.assertRaisesRegex(RuntimeError, "baseline changed"):
                ingest_safety.verify_pre_import(
                    output_dir,
                    self.baseline,
                    db=FakeDb(counts),
                )

    def test_verify_pre_import_rejects_document_owner_collision(self):
        counts = {key: self.baseline[key] for key in ingest_safety.BASELINE_QUERIES}
        existing = {
            "documents": [{
                "doc_id": "doc-1",
                "machine_slug": "birkhaeuser-machine",
                "customer": "Birkhäuser + GBC AG",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            write_stage(output_dir / "machine.jsonl")
            with self.assertRaisesRegex(RuntimeError, "document identity collision"):
                ingest_safety.verify_pre_import(
                    output_dir,
                    self.baseline,
                    db=FakeDb(counts, existing),
                )

    def test_verify_pre_import_allows_resuming_same_document_owner(self):
        counts = {key: self.baseline[key] for key in ingest_safety.BASELINE_QUERIES}
        existing = {
            "machines": [{
                "machine_slug": "new-customer-machine-a",
                "customer": "New Customer",
            }],
            "documents": [{
                "doc_id": "doc-1",
                "machine_slug": "new-customer-machine-a",
                "customer": "New Customer",
            }],
            "ManualSection": [{
                "node_id": "section-1",
                "doc_id": "doc-1",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            write_stage(output_dir / "machine.jsonl")
            ingest_safety.verify_pre_import(
                output_dir,
                self.baseline,
                db=FakeDb(counts, existing),
            )


if __name__ == "__main__":
    unittest.main()
