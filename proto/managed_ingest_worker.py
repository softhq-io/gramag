"""Durable per-document ingestion worker.

Run with ``python -m proto.managed_ingest_worker``. Jobs are leased from the
Proto graph so a restart cannot lose queued work or process one document twice.
"""

from __future__ import annotations

import os
import json
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz

from db_helpers import result_single
from knowledge_service import purge_document, queue_health
from proto.db_proto import proto_db
from proto.ingest import (
    PDFIngestError,
    _document_payload_count,
    clear_document_payload,
    ingest_image_asset,
    ingest_pdf,
    ingest_text_config,
    upsert_category,
)


POLL_SECONDS = float(os.getenv("KNOWLEDGE_WORKER_POLL_SECONDS", "2"))
LEASE_SECONDS = int(os.getenv("KNOWLEDGE_WORKER_LEASE_SECONDS", "900"))
PDF_WORKERS = int(os.getenv("KNOWLEDGE_PDF_WORKERS", "2"))
RETRY_DELAYS = (30, 120, 600)


class IngestionCancelled(RuntimeError):
    pass


def user_safe_ingest_error(error: Exception, *, terminal: bool) -> str:
    if isinstance(error, PDFIngestError):
        return "The PDF could not be fully processed. You can retry it manually." if terminal else (
            "The PDF could not be fully processed; it will retry automatically."
        )
    if isinstance(error, FileNotFoundError) or "original file is missing" in str(error).lower():
        return "The stored original is unavailable. Contact an administrator."
    if terminal:
        return "Document processing failed after automatic retries. You can retry it manually."
    return "Document processing failed; it will retry automatically."


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


class ManagedIngestWorker:
    def __init__(self, worker_id: str | None = None):
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.last_health_log = 0.0

    def heartbeat(self) -> None:
        proto_db.write(
            """
            MERGE (h:WorkerHeartbeat {name: 'managed-ingest'})
            SET h.worker_id = $worker_id, h.updated_at = $now
            """,
            {"worker_id": self.worker_id, "now": iso()},
        )
        if time.monotonic() - self.last_health_log >= 60:
            health = queue_health()
            print(
                "managed_ingest_health "
                + json.dumps(health, sort_keys=True),
                flush=True,
            )
            self.last_health_log = time.monotonic()

    def recover_expired_leases(self) -> None:
        now = iso()
        proto_db.write(
            """
            MATCH (d:Document)-[:CURRENT_JOB]->(j:IngestionJob {status: 'processing'})
            WHERE j.lease_expires_at < $now AND NOT coalesce(j.cancel_requested, false)
            SET j.status = 'queued', j.phase = 'queued', j.next_attempt_at = $now,
                j.worker_id = NULL, j.lease_expires_at = NULL, j.updated_at = $now,
                d.status = 'queued', d.phase = 'queued', d.updated_at = $now
            """,
            {"now": now},
        )
        deleting = result_single(
            proto_db.query(
                """
                MATCH (d:Document {status: 'deleting'})-[:CURRENT_JOB]->(j:IngestionJob)
                WHERE j.lease_expires_at IS NULL OR j.lease_expires_at < $now
                RETURN d.id AS id LIMIT 1
                """,
                {"now": now},
            )
        )
        if deleting:
            purge_document(deleting["id"])

    def claim(self) -> dict | None:
        now = utc_now()
        return result_single(
            proto_db.write(
                """
                MATCH (d:Document)-[:CURRENT_JOB]->(j:IngestionJob {status: 'queued'})
                WHERE coalesce(j.next_attempt_at, '') <= $now
                  AND d.status = 'queued'
                WITH d, j ORDER BY j.created_at
                LIMIT 1
                SET j.status = 'processing', j.phase = 'validating',
                    j.worker_id = $worker_id, j.started_at = coalesce(j.started_at, $now),
                    j.updated_at = $now, j.lease_expires_at = $lease,
                    j.attempt_count = coalesce(j.attempt_count, 0) + 1,
                    d.status = 'processing', d.phase = 'validating',
                    d.updated_at = $now, d.error_message = NULL
                RETURN d.id AS document_id, d.name AS name, d.path AS path,
                       d.storage_rel_path AS storage_rel_path, d.kind AS kind,
                       d.size AS size, d.category AS category,
                       j.id AS job_id, j.attempt_count AS attempt_count
                """,
                {
                    "now": iso(now),
                    "lease": iso(now + timedelta(seconds=LEASE_SECONDS)),
                    "worker_id": self.worker_id,
                },
            )
        )

    def _machine(self, document_id: str) -> dict:
        row = result_single(
            proto_db.query(
                """
                MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document {id: $id})
                RETURN m.slug AS slug, m.folder AS folder, m.customer AS customer
                """,
                {"id": document_id},
            )
        )
        if not row:
            raise RuntimeError("Document is not attached to a machine")
        return row

    def _cancel_requested(self, job_id: str) -> bool:
        row = result_single(
            proto_db.query(
                """
                MATCH (j:IngestionJob {id: $id})
                RETURN coalesce(j.cancel_requested, false) AS cancel_requested
                """,
                {"id": job_id},
            )
        )
        return not row or bool(row.get("cancel_requested"))

    def progress(self, document_id: str, job_id: str, phase: str, current: int, total: int) -> None:
        if self._cancel_requested(job_id):
            raise IngestionCancelled("Document deletion requested")
        now = utc_now()
        proto_db.write(
            """
            MATCH (d:Document {id: $document_id})-[:CURRENT_JOB]->(j:IngestionJob {id: $job_id})
            SET j.phase = $phase, j.progress_current = $current,
                j.progress_total = $total, j.updated_at = $now,
                j.lease_expires_at = $lease,
                d.phase = $phase, d.progress_current = $current,
                d.progress_total = $total, d.updated_at = $now
            """,
            {
                "document_id": document_id,
                "job_id": job_id,
                "phase": phase,
                "current": current,
                "total": total,
                "now": iso(now),
                "lease": iso(now + timedelta(seconds=LEASE_SECONDS)),
            },
        )
        self.heartbeat()

    def process(self, job: dict) -> None:
        document_id = job["document_id"]
        job_id = job["job_id"]
        path = Path(job.get("path") or "")
        if not path.is_file():
            raise RuntimeError("Original file is missing from managed storage")
        machine = self._machine(document_id)
        category = job.get("category") or "Documents"
        category_id = upsert_category(machine["slug"], category)
        file_record = {
            "document_id": document_id,
            "name": job.get("name") or path.name,
            "path": str(path),
            "rel": job.get("storage_rel_path") or path.name,
            "size": int(job.get("size") or path.stat().st_size),
            "category": category,
        }

        clear_document_payload(document_id)
        kind = job.get("kind")
        progress = lambda phase, current, total: self.progress(
            document_id, job_id, phase, current, total
        )
        if kind == "pdf":
            with fitz.open(path) as pdf:
                progress("validating", 0, int(pdf.page_count))
            ingest_pdf(
                machine["slug"],
                category_id,
                file_record,
                workers=PDF_WORKERS,
                progress=progress,
            )
        elif kind == "text":
            progress("extracting", 0, 1)
            ingest_text_config(machine["slug"], category_id, file_record)
            progress("indexing", 1, 1)
        elif kind == "image":
            progress("vision", 0, 1)
            ingest_image_asset(machine["slug"], category_id, file_record)
            progress("indexing", 1, 1)
        else:
            raise RuntimeError(f"Unsupported queued document kind: {kind}")

        stored = _document_payload_count(document_id, kind)
        if stored < 1:
            raise RuntimeError("Ingestion completed without verified searchable content")
        now = iso()
        finalized = result_single(proto_db.write(
            """
            MATCH (d:Document {id: $document_id})-[:CURRENT_JOB]->(j:IngestionJob {id: $job_id})
            WHERE d.status = 'processing' AND NOT coalesce(j.cancel_requested, false)
            SET j.status = 'ready', j.phase = 'ready', j.completed_at = $now,
                j.updated_at = $now, j.lease_expires_at = NULL,
                d.status = 'ready', d.phase = 'ready', d.error_message = NULL,
                d.updated_at = $now, d.ready_at = $now,
                d.retry_count = coalesce(j.attempt_count, 1) - 1
            RETURN d.id AS id
            """,
            {"document_id": document_id, "job_id": job_id, "now": now},
        ))
        if not finalized:
            raise IngestionCancelled("Document deletion requested before finalization")

    def fail(self, job: dict, error: Exception) -> None:
        document_id = job["document_id"]
        job_id = job["job_id"]
        if isinstance(error, IngestionCancelled) or self._cancel_requested(job_id):
            purge_document(document_id)
            return
        attempt = int(job.get("attempt_count") or 1)
        internal_message = str(error).strip()[:500] or error.__class__.__name__
        now = utc_now()
        if attempt <= len(RETRY_DELAYS):
            message = user_safe_ingest_error(error, terminal=False)
            next_attempt = now + timedelta(seconds=RETRY_DELAYS[attempt - 1])
            proto_db.write(
                """
                MATCH (d:Document {id: $document_id})-[:CURRENT_JOB]->(j:IngestionJob {id: $job_id})
                SET j.status = 'queued', j.phase = 'queued', j.error_message = $error,
                    j.next_attempt_at = $next_attempt, j.updated_at = $now,
                    j.worker_id = NULL, j.lease_expires_at = NULL,
                    d.status = 'queued', d.phase = 'queued', d.error_message = $error,
                    d.retry_count = $attempt, d.updated_at = $now
                """,
                {
                    "document_id": document_id,
                    "job_id": job_id,
                    "error": message,
                    "attempt": attempt,
                    "next_attempt": iso(next_attempt),
                    "now": iso(now),
                },
            )
        else:
            message = user_safe_ingest_error(error, terminal=True)
            proto_db.write(
                """
                MATCH (d:Document {id: $document_id})-[:CURRENT_JOB]->(j:IngestionJob {id: $job_id})
                SET j.status = 'failed', j.phase = 'failed', j.error_message = $error,
                    j.completed_at = $now, j.updated_at = $now,
                    j.worker_id = NULL, j.lease_expires_at = NULL,
                    d.status = 'failed', d.phase = 'failed', d.error_message = $error,
                    d.retry_count = $retry_count, d.updated_at = $now
                """,
                {
                    "document_id": document_id,
                    "job_id": job_id,
                    "error": message,
                    "retry_count": len(RETRY_DELAYS),
                    "now": iso(now),
                },
            )
            print(
                f"managed_ingest_terminal_failure document_id={document_id} error={internal_message}",
                flush=True,
            )

    def run_once(self) -> bool:
        self.heartbeat()
        self.recover_expired_leases()
        job = self.claim()
        if not job:
            return False
        try:
            self.process(job)
        except Exception as exc:
            print(f"Managed ingest attempt failed for {job['document_id']}: {exc}", flush=True)
            self.fail(job, exc)
        return True

    def run_forever(self) -> None:
        proto_db.connect()
        print(f"Managed ingest worker started: {self.worker_id}", flush=True)
        while True:
            worked = self.run_once()
            if not worked:
                time.sleep(POLL_SECONDS)


def main() -> None:
    ManagedIngestWorker().run_forever()


if __name__ == "__main__":
    main()
