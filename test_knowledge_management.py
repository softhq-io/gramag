"""Focused tests for managed knowledge configuration and ingestion."""

from __future__ import annotations

import tempfile
import unittest
import os
import asyncio
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROTO_CACHE_DIR", "/tmp/gramag-test-managed-cache")

from fastapi import HTTPException
from starlette.datastructures import UploadFile
from PIL import Image

import knowledge_service
import migrate_managed_knowledge
from proto.managed_ingest_worker import ManagedIngestWorker, RETRY_DELAYS, user_safe_ingest_error


def principal(role: str) -> dict:
    return {"id": "user-1", "role": role, "all_clients": role != "user", "client_ids": []}


class QueryResult:
    def __init__(self, columns: list[str], rows: list[list]):
        self.header = [[1, column] for column in columns]
        self.result_set = rows


class KnowledgeAuthorizationTests(unittest.TestCase):
    def test_only_existing_manager_roles_can_manage_knowledge(self):
        self.assertEqual(
            knowledge_service.require_knowledge_manager(principal("all_clients"))["role"],
            "all_clients",
        )
        self.assertEqual(
            knowledge_service.require_knowledge_manager(principal("superadmin"))["role"],
            "superadmin",
        )
        with self.assertRaises(HTTPException) as ctx:
            knowledge_service.require_knowledge_manager(principal("user"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_all_clients_cannot_activate_client(self):
        with self.assertRaises(HTTPException) as ctx:
            knowledge_service.set_client_active("client-a", True, principal("all_clients"))
        self.assertEqual(ctx.exception.status_code, 403)


class UploadValidationTests(unittest.TestCase):
    def test_category_is_normalized_and_bounded(self):
        self.assertEqual(knowledge_service.validate_category("  Service   Manuals "), "Service Manuals")
        for invalid in ("", "x" * 81, "bad\x00category"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HTTPException):
                    knowledge_service.validate_category(invalid)

    def test_pdf_text_and_image_content_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "manual.pdf"
            import fitz

            document = fitz.open()
            document.new_page()
            document.save(pdf)
            document.close()
            knowledge_service._validate_content(pdf, ".pdf")

            text = root / "config.txt"
            text.write_text("machine configuration", encoding="utf-8")
            knowledge_service._validate_content(text, ".txt")

            image = root / "photo.png"
            Image.new("RGB", (4, 4), "white").save(image)
            knowledge_service._validate_content(image, ".png")
            with self.assertRaises(HTTPException):
                knowledge_service._validate_content(image, ".jpg")

            fake = root / "fake.pdf"
            fake.write_text("not a pdf")
            with self.assertRaises(HTTPException) as ctx:
                knowledge_service._validate_content(fake, ".pdf")
            self.assertEqual(ctx.exception.status_code, 422)

    def test_managed_paths_never_use_original_filename(self):
        self.assertEqual(knowledge_service._safe_segment("../../client/a"), "client-a")
        self.assertNotIn("/", knowledge_service._safe_segment("machine/../../etc"))

    def test_duplicate_filenames_create_distinct_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            import fitz

            pdf = fitz.open()
            pdf.new_page()
            payload = pdf.tobytes()
            pdf.close()
            machine = {"client_id": "client-1", "slug": "machine-slug"}
            captured: list[dict] = []

            def remember(_query, params=None):
                captured.append(params or {})
                return QueryResult([], [])

            with patch.object(knowledge_service, "DOCUMENT_ROOT", root), \
                 patch.object(knowledge_service, "_managed_machine", return_value=machine), \
                 patch.object(knowledge_service, "get_document", side_effect=lambda doc_id, _user: {"id": doc_id}), \
                 patch.object(knowledge_service, "_audit"), \
                 patch.object(knowledge_service.proto_db, "write", side_effect=remember):
                first = asyncio.run(knowledge_service.create_uploaded_document(
                    "machine-1", "Manuals", UploadFile(BytesIO(payload), filename="same.pdf"), principal("all_clients")
                ))
                second = asyncio.run(knowledge_service.create_uploaded_document(
                    "machine-1", "Manuals", UploadFile(BytesIO(payload), filename="same.pdf"), principal("all_clients")
                ))

            self.assertNotEqual(first["id"], second["id"])
            document_params = [params for params in captured if params.get("document_id")]
            self.assertEqual(len(document_params), 2)
            self.assertNotEqual(document_params[0]["path"], document_params[1]["path"])
            self.assertTrue(Path(document_params[0]["path"]).is_file())
            self.assertEqual(Path(document_params[0]["path"]).name, "original")
            self.assertNotIn("same.pdf", document_params[0]["path"])

    def test_streaming_limit_rejects_and_cleans_oversize_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(knowledge_service, "DOCUMENT_ROOT", Path(tmp)), \
             patch.object(knowledge_service, "MAX_UPLOAD_BYTES", 3), \
             patch.object(knowledge_service, "_managed_machine", return_value={"client_id": "c", "slug": "m"}):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(knowledge_service.create_uploaded_document(
                    "machine-1", "Config", UploadFile(BytesIO(b"four"), filename="config.txt"), principal("all_clients")
                ))
            self.assertEqual(ctx.exception.status_code, 413)
            self.assertEqual(list(Path(tmp).rglob("*.tmp")), [])


class WorkerStateTests(unittest.TestCase):
    def test_recent_processing_progress_suppresses_queue_stall(self):
        now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
        health = {
            "actionable_queued": 5,
            "oldest_actionable_at": (now - timedelta(minutes=30)).isoformat(),
            "processing": 1,
            "latest_processing_update_at": (now - timedelta(seconds=30)).isoformat(),
        }
        self.assertFalse(knowledge_service.queue_is_stalled(health, now=now))

    def test_old_actionable_queue_without_progress_is_stalled(self):
        now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
        health = {
            "actionable_queued": 2,
            "oldest_actionable_at": (now - timedelta(minutes=11)).isoformat(),
            "processing": 1,
            "latest_processing_update_at": (now - timedelta(minutes=11)).isoformat(),
        }
        self.assertTrue(knowledge_service.queue_is_stalled(health, now=now))

    def test_retry_backoff_is_not_actionable(self):
        now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
        health = {
            "actionable_queued": 0,
            "oldest_actionable_at": None,
            "processing": 0,
        }
        self.assertFalse(knowledge_service.queue_is_stalled(health, now=now))

    def test_ingest_errors_exposed_to_users_do_not_leak_internal_details(self):
        message = user_safe_ingest_error(
            RuntimeError("API key failure while reading /data/managed-documents/secret"),
            terminal=True,
        )
        self.assertNotIn("/data", message)
        self.assertNotIn("API key", message)

    def test_transient_failure_requeues_before_terminal_failure(self):
        worker = ManagedIngestWorker("test-worker")
        job = {"document_id": "doc-1", "job_id": "job-1", "attempt_count": 1}
        with patch.object(worker, "_cancel_requested", return_value=False), \
             patch.object(knowledge_service.proto_db, "write") as write:
            worker.fail(job, RuntimeError("temporary"))
        cypher = write.call_args.args[0]
        params = write.call_args.args[1]
        self.assertIn("j.status = 'queued'", cypher)
        self.assertEqual(params["attempt"], 1)

        terminal = {**job, "attempt_count": len(RETRY_DELAYS) + 1}
        with patch.object(worker, "_cancel_requested", return_value=False), \
             patch.object(knowledge_service.proto_db, "write") as write:
            worker.fail(terminal, RuntimeError("permanent"))
        self.assertIn("j.status = 'failed'", write.call_args.args[0])

    def test_cancelled_failure_purges_instead_of_requeueing(self):
        worker = ManagedIngestWorker("test-worker")
        job = {"document_id": "doc-1", "job_id": "job-1", "attempt_count": 1}
        with patch.object(worker, "_cancel_requested", return_value=True), \
             patch("proto.managed_ingest_worker.purge_document") as purge:
            worker.fail(job, RuntimeError("cancelled"))
        purge.assert_called_once_with("doc-1")


class LegacyMigrationTests(unittest.TestCase):
    def test_report_separates_linked_and_quarantined_machines(self):
        result = QueryResult(
            ["slug", "erp_id", "client_id", "link_mode", "customer", "documents"],
            [
                ["linked", "machine-1", "client-1", "single", "Client", 2],
                ["unknown", None, None, None, "Unknown", 3],
            ],
        )
        with patch.object(migrate_managed_knowledge.proto_db, "query", return_value=result):
            report = migrate_managed_knowledge.build_report()
        self.assertEqual(report["linked_machine_count"], 1)
        self.assertEqual(report["quarantined_machine_count"], 1)
        self.assertEqual(report["document_count"], 5)
        self.assertEqual(report["clients_to_activate"], ["client-1"])

    def test_mapping_file_supports_grouped_and_exact_crm_machines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.json"
            path.write_text(json.dumps({
                "grouped": "client-1",
                "exact": {"client_id": "client-2", "erp_machine_id": "machine-2"},
            }))
            mappings = migrate_managed_knowledge.load_mappings(path)
        self.assertEqual(mappings["grouped"], {"client_id": "client-1", "erp_machine_id": None})
        self.assertEqual(mappings["exact"]["erp_machine_id"], "machine-2")


if __name__ == "__main__":
    unittest.main()
