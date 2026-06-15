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

    def test_ingest_pdf_raises_on_section_write_failure(self):
        originals = {
            "upsert_document": ingest.upsert_document,
            "render_pdf_pages": ingest.render_pdf_pages,
            "clear_document_payload": ingest.clear_document_payload,
            "generate_embeddings_batch": ingest.generate_embeddings_batch,
            "proto_db": ingest.proto_db,
        }

        class FailingDb:
            def write(self, *_args, **_kwargs):
                raise RuntimeError("connection closed by server")

        try:
            ingest.upsert_document = lambda *args, **kwargs: "doc-id"
            ingest.render_pdf_pages = lambda *args, **kwargs: [
                {"page": 1, "text": "manual text", "png_path": "pages/doc-id/p0001.png"}
            ]
            ingest.clear_document_payload = lambda doc_id: None
            ingest.generate_embeddings_batch = lambda texts: [[0.1, 0.2, 0.3]]
            ingest.proto_db = FailingDb()

            with redirect_stdout(StringIO()):
                with self.assertRaisesRegex(ingest.PDFIngestError, "section write failed p1"):
                    ingest.ingest_pdf(
                        "machine-slug",
                        "category-id",
                        {"rel": "manual.pdf", "path": "manual.pdf", "name": "manual.pdf", "category": "Manuals", "size": 1},
                    )
        finally:
            for name, value in originals.items():
                setattr(ingest, name, value)

    def test_pdf_checkpoint_requires_expected_graph_sections(self):
        originals = {
            "_source_fingerprint": ingest._source_fingerprint,
            "_pdf_page_count": ingest._pdf_page_count,
            "_document_payload_count": ingest._document_payload_count,
        }
        try:
            ingest._source_fingerprint = lambda f: "fingerprint"
            ingest._pdf_page_count = lambda f: 5
            ingest._document_payload_count = lambda doc_id, kind: 3

            done = {
                "pdf::manual.pdf": {
                    "sections": 3,
                    "ts": 1,
                    "fingerprint": "fingerprint",
                }
            }

            self.assertFalse(
                ingest._checkpoint_done(
                    done,
                    "pdf::manual.pdf",
                    {"rel": "manual.pdf", "path": "manual.pdf"},
                    machine_slug="machine-slug",
                    kind="pdf",
                )
            )
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
