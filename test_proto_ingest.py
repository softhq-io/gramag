from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

os.environ.setdefault("PROTO_CACHE_DIR", "/private/tmp/gramag-proto-test-cache")
os.environ.setdefault("PROTO_ROOT", "/private/tmp/gramag-proto-test-source")
os.environ.setdefault("PROTO_MANIFEST_PATH", "/private/tmp/gramag-proto-test-manifest.json")

import proto.ingest as ingest


class ProtoIngestTests(unittest.TestCase):
    def test_save_checkpoint_is_atomic(self):
        original = ingest.CHECKPOINT
        with tempfile.TemporaryDirectory() as tmp:
            ingest.CHECKPOINT = Path(tmp) / "ingest_checkpoint.json"

            ingest._save_checkpoint({"done": {"machine": {"pdf::manual.pdf": {"ok": 1}}}})

            self.assertEqual(
                json.loads(ingest.CHECKPOINT.read_text()),
                {"done": {"machine": {"pdf::manual.pdf": {"ok": 1}}}},
            )
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
        ingest.CHECKPOINT = original

    def test_parse_kinds_validates_supported_kinds(self):
        self.assertEqual(ingest.parse_kinds("pdf, text"), {"pdf", "text"})
        with self.assertRaises(ingest.argparse.ArgumentTypeError):
            ingest.parse_kinds("pdf,video")

    def test_ingest_machine_filters_by_kind(self):
        calls = []
        originals = {
            "upsert_machine": ingest.upsert_machine,
            "upsert_category": ingest.upsert_category,
            "ingest_pdf": ingest.ingest_pdf,
            "ingest_text_config": ingest.ingest_text_config,
            "ingest_image_asset": ingest.ingest_image_asset,
            "_checkpoint_done": ingest._checkpoint_done,
            "_save_checkpoint": ingest._save_checkpoint,
            "_source_fingerprint": ingest._source_fingerprint,
        }
        try:
            ingest.upsert_machine = lambda m: m["slug"]
            ingest.upsert_category = lambda slug, cat: f"{slug}:{cat}"
            ingest.ingest_pdf = lambda *args, **kwargs: calls.append("pdf") or 1
            ingest.ingest_text_config = lambda *args, **kwargs: calls.append("text") or 1
            ingest.ingest_image_asset = lambda *args, **kwargs: calls.append("image") or 1
            ingest._checkpoint_done = lambda *args, **kwargs: False
            ingest._save_checkpoint = lambda cp: None
            ingest._source_fingerprint = lambda f: "fingerprint"

            machine = {
                "slug": "birk-machine",
                "folder": "Birk Machine",
                "categories": ["Manuals"],
                "files": {
                    "pdf": [{"rel": "manual.pdf", "name": "manual.pdf", "category": "Manuals", "size": 1}],
                    "text": [{"rel": "config.txt", "name": "config.txt", "category": "Manuals", "size": 1}],
                    "image": [{"rel": "image.jpg", "name": "image.jpg", "category": "Manuals", "size": 1}],
                },
            }

            with redirect_stdout(StringIO()):
                ingest.ingest_machine(machine, {"done": {}}, kinds={"pdf", "text"})

            self.assertEqual(calls, ["pdf", "text"])
        finally:
            for name, value in originals.items():
                setattr(ingest, name, value)

    def test_document_ids_are_distinct_for_disjoint_machine_shards(self):
        self.assertNotEqual(
            ingest._id("birkhaeuser-machine-a", "manual.pdf"),
            ingest._id("birkhaeuser-machine-b", "manual.pdf"),
        )


if __name__ == "__main__":
    unittest.main()
