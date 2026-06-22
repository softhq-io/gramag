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
from proto.db_proto import ProtoGraphConnection


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

    def test_stage_pdf_records_writes_durable_document_and_sections(self):
        originals = {
            "render_pdf_pages": ingest.render_pdf_pages,
            "_vision_for_page": ingest._vision_for_page,
            "generate_embeddings_batch": ingest.generate_embeddings_batch,
            "_source_fingerprint": ingest._source_fingerprint,
        }
        try:
            ingest.render_pdf_pages = lambda *_args, **_kwargs: [
                {"page": 1, "text": "page one", "png_path": "p1.png"},
                {"page": 2, "text": "page two", "png_path": "p2.png"},
            ]
            ingest._vision_for_page = lambda page, *_args, **_kwargs: (page["page"], f"vision {page['page']}")
            ingest.generate_embeddings_batch = lambda texts: [[0.1], [0.2]]
            ingest._source_fingerprint = lambda f: "fingerprint"

            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "machine.jsonl"
                count = ingest.stage_pdf_records(
                    {
                        "slug": "machine",
                        "folder": "Machine",
                        "type": "Falzanlage",
                        "model": None,
                        "serial": None,
                        "raw": "Machine",
                        "path": "/source/Machine",
                    },
                    "cat",
                    {"rel": "manual.pdf", "path": "manual.pdf", "name": "manual.pdf", "category": "Manuals", "size": 1},
                    output,
                    workers=1,
                )

                records = [json.loads(line) for line in output.read_text().splitlines()]

            self.assertEqual(count, 2)
            self.assertEqual([record["record"] for record in records], ["document", "manual_section", "manual_section"])
            self.assertEqual(records[0]["expected_sections"], 2)
        finally:
            for name, value in originals.items():
                setattr(ingest, name, value)

    def test_import_staged_records_checkpoints_after_successful_write(self):
        calls = []
        originals = {
            "upsert_machine": ingest.upsert_machine,
            "upsert_category": ingest.upsert_category,
            "upsert_document": ingest.upsert_document,
            "clear_document_payload": ingest.clear_document_payload,
            "proto_db": ingest.proto_db,
        }

        class FakeDb:
            def write(self, query, params):
                calls.append(("write", params["id"]))

        try:
            ingest.upsert_machine = lambda machine: calls.append(("machine", machine["slug"])) or machine["slug"]
            ingest.upsert_category = lambda slug, category: calls.append(("category", category)) or f"{slug}:{category}"
            ingest.upsert_document = lambda slug, cat_id, f, kind: calls.append(("document", f["rel"], kind)) or ingest._id(slug, f["rel"])
            ingest.clear_document_payload = lambda doc_id: calls.append(("clear", doc_id))
            ingest.proto_db = FakeDb()

            with tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp)
                checkpoint = output_dir / "import_checkpoint.json"
                doc_id = ingest._id("machine", "manual.pdf")
                staged = output_dir / "machine.jsonl"
                staged.write_text(
                    "\n".join([
                        json.dumps({
                            "schema": 1,
                            "machine": {
                                "slug": "machine",
                                "folder": "Machine",
                                "type": "Falzanlage",
                                "model": None,
                                "serial": None,
                                "raw": "Machine",
                                "path": "/source/Machine",
                            },
                            "category": "Manuals",
                            "doc_id": doc_id,
                            "kind": "pdf",
                            "file": {"name": "manual.pdf", "rel": "manual.pdf", "path": "manual.pdf", "category": "Manuals"},
                            "fingerprint": "fingerprint",
                            "record": "document",
                        }),
                        json.dumps({
                            "schema": 1,
                            "machine": {
                                "slug": "machine",
                                "folder": "Machine",
                                "type": "Falzanlage",
                                "model": None,
                                "serial": None,
                                "raw": "Machine",
                                "path": "/source/Machine",
                            },
                            "category": "Manuals",
                            "doc_id": doc_id,
                            "kind": "pdf",
                            "file": {"name": "manual.pdf", "rel": "manual.pdf", "path": "manual.pdf", "category": "Manuals"},
                            "fingerprint": "fingerprint",
                            "record": "manual_section",
                            "section": {
                                "id": ingest._id(doc_id, "p1"),
                                "page": 1,
                                "text": "page",
                                "vision_desc": "",
                                "merged": "page",
                                "png_path": "p1.png",
                            },
                            "embedding": [0.1],
                        }),
                    ]) + "\n",
                    encoding="utf-8",
                )

                with redirect_stdout(StringIO()):
                    ingest.import_staged_records(output_dir, checkpoint)
                first_checkpoint = json.loads(checkpoint.read_text())
                with redirect_stdout(StringIO()):
                    ingest.import_staged_records(output_dir, checkpoint)

            self.assertEqual(len(first_checkpoint["done"]), 2)
            self.assertEqual(calls.count(("clear", doc_id)), 1)
            self.assertEqual(calls.count(("write", ingest._id(doc_id, "p1"))), 1)
            self.assertEqual(calls.count(("document", "manual.pdf", "pdf")), 1)
        finally:
            for name, value in originals.items():
                setattr(ingest, name, value)

    def test_import_staged_records_resumed_section_ensures_document_once(self):
        calls = []
        originals = {
            "upsert_machine": ingest.upsert_machine,
            "upsert_category": ingest.upsert_category,
            "upsert_document": ingest.upsert_document,
            "proto_db": ingest.proto_db,
        }

        class FakeDb:
            def write(self, query, params):
                calls.append(("write", params["id"]))

        try:
            ingest.upsert_machine = lambda machine: calls.append(("machine", machine["slug"])) or machine["slug"]
            ingest.upsert_category = lambda slug, category: calls.append(("category", category)) or f"{slug}:{category}"
            ingest.upsert_document = lambda slug, cat_id, f, kind: calls.append(("document", f["rel"], kind)) or ingest._id(slug, f["rel"])
            ingest.proto_db = FakeDb()

            with tempfile.TemporaryDirectory() as tmp:
                output_dir = Path(tmp)
                checkpoint = output_dir / "import_checkpoint.json"
                doc_id = ingest._id("machine", "manual.pdf")
                staged = output_dir / "machine.jsonl"
                staged.write_text(
                    "\n".join(
                        json.dumps({
                            "schema": 1,
                            "machine": {
                                "slug": "machine",
                                "folder": "Machine",
                                "type": "Falzanlage",
                                "model": None,
                                "serial": None,
                                "raw": "Machine",
                                "path": "/source/Machine",
                            },
                            "category": "Manuals",
                            "doc_id": doc_id,
                            "kind": "pdf",
                            "file": {"name": "manual.pdf", "rel": "manual.pdf", "path": "manual.pdf", "category": "Manuals"},
                            "fingerprint": "fingerprint",
                            "record": "manual_section",
                            "section": {
                                "id": ingest._id(doc_id, f"p{page}"),
                                "page": page,
                                "text": "page",
                                "vision_desc": "",
                                "merged": "page",
                                "png_path": f"p{page}.png",
                            },
                            "embedding": [0.1],
                        })
                        for page in (1, 2)
                    ) + "\n",
                    encoding="utf-8",
                )

                with redirect_stdout(StringIO()):
                    ingest.import_staged_records(output_dir, checkpoint)

            self.assertEqual(calls.count(("document", "manual.pdf", "pdf")), 1)
            self.assertEqual(calls.count(("write", ingest._id(doc_id, "p1"))), 1)
            self.assertEqual(calls.count(("write", ingest._id(doc_id, "p2"))), 1)
        finally:
            for name, value in originals.items():
                setattr(ingest, name, value)

    def test_proto_db_retry_classifier_catches_redis_loading(self):
        db = ProtoGraphConnection()
        self.assertTrue(db._is_retryable_error(RuntimeError("Redis is loading the dataset in memory")))
        self.assertTrue(db._is_retryable_error(RuntimeError("Connection closed by server")))
        self.assertFalse(db._is_retryable_error(RuntimeError("syntax error")))

    def test_proto_db_retry_resets_without_reconnect_escape(self):
        db = ProtoGraphConnection()
        attempts = []
        resets = []

        def flaky_query():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("Timeout reading from socket")
            return "ok"

        db.reset = lambda: resets.append(1)
        db.reconnect = lambda: (_ for _ in ()).throw(AssertionError("reconnect should not run during retry"))

        self.assertEqual(db._with_retry(flaky_query, max_retries=1), "ok")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(resets), 1)


if __name__ == "__main__":
    unittest.main()
