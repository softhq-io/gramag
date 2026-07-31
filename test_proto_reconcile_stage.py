from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("PROTO_CACHE_DIR", "/private/tmp/gramag-proto-test-cache")
os.environ.setdefault("PROTO_ROOT", "/private/tmp/gramag-proto-test-source")
os.environ.setdefault(
    "PROTO_MANIFEST_PATH",
    "/private/tmp/gramag-proto-test-manifest.json",
)

from proto.reconcile_stage import (
    reconcile_staged_records,
    verify_extraction_markers,
)


def _machine(files: list[dict]) -> dict:
    return {
        "slug": "customer-machine",
        "files": {"pdf": files, "text": [], "image": []},
    }


def _record(rel: str, fingerprint: str, record_type: str, *, page: int = 1) -> dict:
    doc_id = f"doc-{rel}"
    record = {
        "machine": {"slug": "customer-machine", "customer": "Customer"},
        "kind": "pdf",
        "file": {"name": Path(rel).name, "rel": rel},
        "fingerprint": fingerprint,
        "record": record_type,
        "doc_id": doc_id,
    }
    if record_type == "document":
        record["expected_sections"] = 1
    else:
        record["section"] = {"id": f"{doc_id}-p{page}", "page": page}
    return record


class ProtoReconcileStageTests(unittest.TestCase):
    def test_extraction_markers_must_match_every_current_manifest_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text('{"machines": []}')
            import hashlib

            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            marker = root / "marker.json"
            marker.write_text(
                json.dumps(
                    {
                        "manifest_path": str(manifest),
                        "manifest_sha256": digest,
                    }
                )
            )

            verified = verify_extraction_markers([marker], [manifest])
            self.assertEqual(verified[str(manifest.resolve())], str(marker.resolve()))

            manifest.write_text('{"machines": [1]}')
            with self.assertRaisesRegex(RuntimeError, "manifest hash is stale"):
                verify_extraction_markers([marker], [manifest])

    def test_reconcile_keeps_only_current_fingerprints_and_is_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "stage"
            output_dir = root / "ready"
            input_dir.mkdir()
            current = {
                "name": "current.pdf",
                "rel": "Manuals/current.pdf",
                "path": "/missing/current.pdf",
                "size": 10,
                "mtime": 2,
            }
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"machines": [_machine([current])]}))
            fingerprint = "Manuals/current.pdf|10|2"
            stale_fingerprint = "Manuals/deleted.pdf|5|1"
            records = [
                _record("Manuals/deleted.pdf", stale_fingerprint, "document"),
                _record("Manuals/deleted.pdf", stale_fingerprint, "manual_section"),
                _record("Manuals/current.pdf", fingerprint, "document"),
                _record("Manuals/current.pdf", fingerprint, "manual_section"),
            ]
            (input_dir / "customer-machine.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records)
            )

            report = reconcile_staged_records(
                input_dir,
                output_dir,
                [manifest],
                kinds={"pdf"},
            )
            reused = reconcile_staged_records(
                input_dir,
                output_dir,
                [manifest],
                kinds={"pdf"},
            )
            reconciled = [
                json.loads(line)
                for line in (output_dir / "customer-machine.jsonl")
                .read_text()
                .splitlines()
            ]

            self.assertEqual(report["written_records"], 2)
            self.assertEqual(report["stale_or_excluded_records"], 2)
            self.assertEqual(reused, report)
            self.assertEqual(
                {record["file"]["rel"] for record in reconciled},
                {"Manuals/current.pdf"},
            )

    def test_reconcile_exclusion_must_match_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "stage"
            input_dir.mkdir()
            omitted = {
                "name": "omit.pdf",
                "rel": "Manuals/omit.pdf",
                "path": "/missing/omit.pdf",
                "size": 10,
                "mtime": 2,
            }
            current = {
                "name": "current.pdf",
                "rel": "Manuals/current.pdf",
                "path": "/missing/current.pdf",
                "size": 10,
                "mtime": 2,
            }
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"machines": [_machine([omitted, current])]}))
            fingerprint = "Manuals/current.pdf|10|2"
            (input_dir / "customer-machine.jsonl").write_text(
                json.dumps(_record("Manuals/current.pdf", fingerprint, "document"))
                + "\n"
                + json.dumps(
                    _record("Manuals/current.pdf", fingerprint, "manual_section")
                )
                + "\n"
            )

            report = reconcile_staged_records(
                input_dir,
                root / "ready",
                [manifest],
                kinds={"pdf"},
                exclude_file_names=["omit.pdf"],
            )

            self.assertEqual(report["current_sources"], 1)
            with self.assertRaisesRegex(RuntimeError, "matched 0 current files"):
                reconcile_staged_records(
                    input_dir,
                    root / "other",
                    [manifest],
                    kinds={"pdf"},
                    exclude_file_names=["missing.pdf"],
                )

    def test_reconcile_fails_before_writing_incomplete_current_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "stage"
            output_dir = root / "ready"
            input_dir.mkdir()
            current = {
                "name": "current.pdf",
                "rel": "Manuals/current.pdf",
                "path": "/missing/current.pdf",
                "size": 10,
                "mtime": 2,
            }
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"machines": [_machine([current])]}))

            with self.assertRaisesRegex(RuntimeError, "incomplete current sources"):
                reconcile_staged_records(
                    input_dir,
                    output_dir,
                    [manifest],
                    kinds={"pdf"},
                )
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
