"""CRM-backed knowledge configuration and managed document lifecycle."""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
from fastapi import HTTPException, UploadFile
from PIL import Image

from db import db
from db_helpers import result_single, result_to_dicts
from proto import PROTO_CACHE_DIR
from proto.db_proto import proto_db


MANAGER_ROLES = {"superadmin", "all_clients"}
MAX_UPLOAD_BYTES = int(os.getenv("KNOWLEDGE_MAX_UPLOAD_BYTES", str(250 * 1024 * 1024)))
DOCUMENT_ROOT = Path(os.getenv("KNOWLEDGE_DOCUMENT_ROOT", "/data/managed-documents"))
APP_DATA_ROOT = Path(os.getenv("APP_DATA_ROOT", "/data"))
UPLOAD_CHUNK_BYTES = 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf": "pdf",
    ".txt": "text",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".bmp": "image",
    ".tif": "image",
    ".tiff": "image",
    ".pcx": "image",
}
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pcx": "image/x-pcx",
}
SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_knowledge_manager(user: dict) -> dict:
    if user.get("role") not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="Knowledge management access required")
    return user


def _audit(user: dict, action: str, target_type: str, target_id: str, details: str = "") -> None:
    proto_db.write(
        """
        CREATE (:KnowledgeAuditEvent {
          id: $id, actor_user_id: $actor, actor_role: $role,
          action: $action, target_type: $target_type, target_id: $target_id,
          details: $details, created_at: $created_at
        })
        """,
        {
            "id": f"knowledge_audit_{uuid.uuid4().hex}",
            "actor": user["id"],
            "role": user.get("role"),
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": details,
            "created_at": now_iso(),
        },
    )


def _crm_client(client_id: str) -> dict:
    row = result_single(
        db.query(
            """
            MATCH (c:Customer {erp_id: $id})
            OPTIONAL MATCH (c)-[:OWNS]->(m:Machine)
            RETURN c.erp_id AS id, c.name AS name, count(DISTINCT m) AS machine_count
            """,
            {"id": client_id},
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="CRM client not found")
    return row


def _crm_machine(client_id: str, machine_id: str) -> dict:
    row = result_single(
        db.query(
            """
            MATCH (c:Customer {erp_id: $client_id})-[:OWNS]->(m:Machine {erp_id: $machine_id})
            RETURN c.erp_id AS client_id, c.name AS client_name,
                   m.erp_id AS erp_id, m.title AS name,
                   m.serial_number AS serial, m.new_erp_nummer AS number
            """,
            {"client_id": client_id, "machine_id": machine_id},
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="CRM machine not found for client")
    return row


def _active_client(client_id: str) -> dict | None:
    return result_single(
        proto_db.query(
            """
            MATCH (c:Customer {erp_id: $id})
            RETURN c.erp_id AS id, c.name AS name, coalesce(c.active, false) AS active
            """,
            {"id": client_id},
        )
    )


def list_clients(user: dict, *, q: str = "", include_inactive: bool = False) -> list[dict]:
    require_knowledge_manager(user)
    if include_inactive and user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Only superadmins can list inactive clients")
    needle = q.strip().lower()
    crm_rows = result_to_dicts(
        db.query(
            """
            MATCH (c:Customer)
            OPTIONAL MATCH (c)-[:OWNS]->(m:Machine)
            WITH c, count(DISTINCT m) AS machine_count
            WHERE $q = '' OR toLower(coalesce(c.name, '')) CONTAINS $q
                         OR toLower(coalesce(c.erp_id, '')) CONTAINS $q
            RETURN c.erp_id AS id, c.name AS name, machine_count
            ORDER BY toLower(coalesce(c.name, c.erp_id))
            LIMIT 500
            """,
            {"q": needle},
        )
    )
    states = {
        row["id"]: row
        for row in result_to_dicts(
            proto_db.query(
                """
                MATCH (c:Customer)
                WHERE c.erp_id IS NOT NULL
                OPTIONAL MATCH (c)-[:HAS_MACHINE]->(m:Machine)
                RETURN c.erp_id AS id, coalesce(c.active, false) AS active,
                       count(DISTINCT CASE WHEN coalesce(m.selected, false) THEN m ELSE NULL END) AS selected_machines
                """
            )
        )
    }
    out = []
    for client in crm_rows:
        state = states.get(client["id"], {})
        item = {
            **client,
            "active": bool(state.get("active", False)),
            "selected_machine_count": int(state.get("selected_machines") or 0),
        }
        if include_inactive or item["active"]:
            out.append(item)
    return out


def set_client_active(client_id: str, active: bool, user: dict) -> dict:
    require_knowledge_manager(user)
    if user.get("role") != "superadmin":
        raise HTTPException(status_code=403, detail="Only superadmins can activate clients")
    client = _crm_client(client_id)
    timestamp = now_iso()
    row = result_single(
        proto_db.write(
            """
            MERGE (c:Customer {erp_id: $id})
            ON CREATE SET c.id = $graph_id, c.created_at = $now
            SET c.name = $name, c.active = $active, c.updated_at = $now,
                c.updated_by_id = $actor
            RETURN c.erp_id AS id, c.name AS name, c.active AS active
            """,
            {
                "id": client_id,
                "graph_id": f"erp:{client_id}",
                "name": client.get("name") or client_id,
                "active": active,
                "now": timestamp,
                "actor": user["id"],
            },
        )
    )
    if active:
        proto_db.write(
            """
            MATCH (c:Customer {erp_id: $id}), (m:Machine {erp_customer_id: $id})
            MERGE (c)-[:HAS_MACHINE]->(m)
            SET m.selected = coalesce(m.selected, true), m.quarantined = false
            """,
            {"id": client_id},
        )
        proto_db.write(
            """
            MATCH (m:Machine {erp_customer_id: $id})-[:HAS_DOCUMENT]->(d:Document)
            SET d.status = coalesce(d.status, 'ready'),
                d.phase = coalesce(d.phase, 'ready'),
                d.source = coalesce(d.source, 'sharepoint_legacy'),
                d.original_name = coalesce(d.original_name, d.name)
            """,
            {"id": client_id},
        )
        proto_db.write(
            """
            MATCH (old:Customer)-[r:HAS_MACHINE]->(m:Machine {erp_customer_id: $id})
            WHERE coalesce(old.erp_id, '') <> $id
            DELETE r
            """,
            {"id": client_id},
        )
    _audit(user, "client_activated" if active else "client_deactivated", "client", client_id)
    return {**client, **(row or {}), "selected_machine_count": 0}


def _machine_state_rows(client_id: str) -> dict[str, dict]:
    return {
        row["erp_id"]: row
        for row in result_to_dicts(
            proto_db.query(
                """
                MATCH (c:Customer {erp_id: $client_id})-[:HAS_MACHINE]->(m:Machine)
                OPTIONAL MATCH (m)-[:HAS_DOCUMENT]->(d:Document)
                RETURN m.erp_id AS erp_id, m.slug AS slug,
                       coalesce(m.selected, false) AS selected,
                       count(DISTINCT d) AS document_count,
                       count(DISTINCT CASE WHEN d.status = 'ready' THEN d ELSE NULL END) AS ready_document_count
                """,
                {"client_id": client_id},
            )
        )
        if row.get("erp_id")
    }


def list_client_machines(client_id: str, user: dict, *, q: str = "") -> list[dict]:
    require_knowledge_manager(user)
    active = _active_client(client_id)
    if not active or not active.get("active"):
        raise HTTPException(status_code=404, detail="Active knowledge client not found")
    needle = q.strip().lower()
    crm_rows = result_to_dicts(
        db.query(
            """
            MATCH (c:Customer {erp_id: $client_id})-[:OWNS]->(m:Machine)
            WHERE $q = '' OR toLower(coalesce(m.title, '')) CONTAINS $q
                         OR toLower(coalesce(m.erp_id, '')) CONTAINS $q
                         OR toLower(coalesce(m.serial_number, '')) CONTAINS $q
            RETURN m.erp_id AS erp_id, m.title AS name,
                   m.serial_number AS serial, m.new_erp_nummer AS number
            ORDER BY toLower(coalesce(m.title, m.erp_id))
            LIMIT 1000
            """,
            {"client_id": client_id, "q": needle},
        )
    )
    states = _machine_state_rows(client_id)
    out = [
        {
            **machine,
            "selected": bool(states.get(machine["erp_id"], {}).get("selected", False)),
            "slug": states.get(machine["erp_id"], {}).get("slug"),
            "document_count": int(states.get(machine["erp_id"], {}).get("document_count") or 0),
            "ready_document_count": int(states.get(machine["erp_id"], {}).get("ready_document_count") or 0),
        }
        for machine in crm_rows
    ]
    legacy_rows = result_to_dicts(
        proto_db.query(
            """
            MATCH (c:Customer {erp_id: $client_id})-[:HAS_MACHINE]->(m:Machine)
            WHERE m.erp_id IS NULL
              AND ($q = '' OR toLower(coalesce(m.folder, '')) CONTAINS $q
                           OR toLower(coalesce(m.serial, '')) CONTAINS $q)
            OPTIONAL MATCH (m)-[:HAS_DOCUMENT]->(d:Document)
            RETURN 'legacy:' + m.slug AS erp_id, m.folder AS name,
                   m.serial AS serial, NULL AS number, coalesce(m.selected, false) AS selected,
                   m.slug AS slug, true AS legacy,
                   count(DISTINCT d) AS document_count,
                   count(DISTINCT CASE WHEN coalesce(d.status, 'ready') = 'ready' THEN d ELSE NULL END) AS ready_document_count
            ORDER BY toLower(m.folder)
            """,
            {"client_id": client_id, "q": needle},
        )
    )
    return out + legacy_rows


def _machine_slug(machine_id: str) -> str:
    return f"erp-{uuid.uuid5(uuid.NAMESPACE_URL, f'gramag:machine:{machine_id}').hex}"


def set_machine_selected(client_id: str, machine_id: str, selected: bool, user: dict) -> dict:
    require_knowledge_manager(user)
    active = _active_client(client_id)
    if not active or not active.get("active"):
        raise HTTPException(status_code=404, detail="Active knowledge client not found")
    timestamp = now_iso()
    if machine_id.startswith("legacy:"):
        slug = machine_id.removeprefix("legacy:")
        row = result_single(
            proto_db.write(
                """
                MATCH (c:Customer {erp_id: $client_id})-[:HAS_MACHINE]->(m:Machine {slug: $slug})
                WHERE m.erp_id IS NULL
                SET m.selected = $selected, m.selection_updated_at = $now,
                    m.selection_updated_by_id = $actor
                RETURN 'legacy:' + m.slug AS erp_id, m.slug AS slug,
                       m.folder AS name, m.serial AS serial,
                       m.selected AS selected, true AS legacy
                """,
                {
                    "client_id": client_id,
                    "slug": slug,
                    "selected": selected,
                    "now": timestamp,
                    "actor": user["id"],
                },
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="Legacy knowledge machine not found")
        _audit(user, "machine_selected" if selected else "machine_deselected", "machine", machine_id)
        return {**row, "document_count": 0, "ready_document_count": 0}

    machine = _crm_machine(client_id, machine_id)
    existing = result_single(
        proto_db.query(
            "MATCH (m:Machine {erp_id: $id}) RETURN m.slug AS slug",
            {"id": machine_id},
        )
    )
    slug = (existing or {}).get("slug") or _machine_slug(machine_id)
    proto_db.write(
        """
        MATCH (old:Customer)-[r:HAS_MACHINE]->(m:Machine {erp_id: $machine_id})
        WHERE coalesce(old.erp_id, '') <> $client_id
        DELETE r
        """,
        {"machine_id": machine_id, "client_id": client_id},
    )
    row = result_single(
        proto_db.write(
            """
            MATCH (c:Customer {erp_id: $client_id})
            MERGE (m:Machine {slug: $slug})
            SET m.erp_id = $machine_id, m.erp_customer_id = $client_id,
                m.customer = $client_name, m.folder = $name,
                m.raw = $name, m.type = coalesce(m.type, $name),
                m.serial = $serial, m.selected = $selected,
                m.selection_updated_at = $now, m.selection_updated_by_id = $actor
            MERGE (c)-[:HAS_MACHINE]->(m)
            RETURN m.erp_id AS erp_id, m.slug AS slug, m.folder AS name,
                   m.serial AS serial, m.selected AS selected
            """,
            {
                "client_id": client_id,
                "client_name": machine.get("client_name") or client_id,
                "machine_id": machine_id,
                "slug": slug,
                "name": machine.get("name") or machine_id,
                "serial": machine.get("serial"),
                "selected": selected,
                "now": timestamp,
                "actor": user["id"],
            },
        )
    )
    _audit(user, "machine_selected" if selected else "machine_deselected", "machine", machine_id)
    return {**machine, **(row or {}), "document_count": 0, "ready_document_count": 0}


def _managed_machine(machine_id: str) -> dict:
    if machine_id.startswith("legacy:"):
        slug = machine_id.removeprefix("legacy:")
        row = result_single(
            proto_db.query(
                """
                MATCH (c:Customer)-[:HAS_MACHINE]->(m:Machine {slug: $slug})
                WHERE m.erp_id IS NULL AND coalesce(c.active, false) AND coalesce(m.selected, false)
                RETURN c.erp_id AS client_id, c.name AS client_name,
                       'legacy:' + m.slug AS erp_id, m.slug AS slug, m.folder AS name
                LIMIT 1
                """,
                {"slug": slug},
            )
        )
        if not row:
            raise HTTPException(status_code=404, detail="Selected legacy knowledge machine not found")
        return row
    row = result_single(
        proto_db.query(
            """
            MATCH (c:Customer)-[:HAS_MACHINE]->(m:Machine {erp_id: $machine_id})
            WHERE coalesce(c.active, false) AND coalesce(m.selected, false)
            RETURN c.erp_id AS client_id, c.name AS client_name,
                   m.erp_id AS erp_id, m.slug AS slug, m.folder AS name
            LIMIT 1
            """,
            {"machine_id": machine_id},
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Selected knowledge machine not found")
    return row


def validate_category(value: str) -> str:
    category = " ".join(value.strip().split())
    if not category or len(category) > 80 or any(ord(ch) < 32 for ch in category):
        raise HTTPException(status_code=422, detail="Category must contain 1–80 printable characters")
    return category


def _safe_segment(value: str) -> str:
    return SAFE_ID_RE.sub("-", value).strip("-._") or "unknown"


def _validate_content(path: Path, extension: str) -> None:
    try:
        if extension == ".pdf":
            with path.open("rb") as source:
                signature = source.read(5)
            if signature != b"%PDF-":
                raise ValueError("missing PDF signature")
            doc = fitz.open(path)
            try:
                if doc.page_count < 1:
                    raise ValueError("PDF has no pages")
            finally:
                doc.close()
        elif extension == ".txt":
            decoder = codecs.getincrementaldecoder("utf-8")()
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    decoder.decode(chunk)
                decoder.decode(b"", final=True)
        else:
            with Image.open(path) as image:
                expected_formats = {
                    ".jpg": {"JPEG"}, ".jpeg": {"JPEG"}, ".png": {"PNG"},
                    ".gif": {"GIF"}, ".bmp": {"BMP"},
                    ".tif": {"TIFF"}, ".tiff": {"TIFF"}, ".pcx": {"PCX"},
                }
                if image.format not in expected_formats[extension]:
                    raise ValueError(f"detected {image.format or 'unknown'} image content")
                if image.width * image.height > 100_000_000:
                    raise ValueError("image dimensions are too large")
                image.verify()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"File content does not match {extension} format") from exc


async def create_uploaded_document(
    machine_id: str,
    category_value: str,
    upload: UploadFile,
    user: dict,
) -> dict:
    require_knowledge_manager(user)
    machine = _managed_machine(machine_id)
    category = validate_category(category_value)
    original_name = Path(upload.filename or "").name
    extension = Path(original_name).suffix.lower()
    kind = ALLOWED_EXTENSIONS.get(extension)
    if (
        not original_name
        or len(original_name) > 255
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in original_name)
        or not kind
    ):
        raise HTTPException(status_code=422, detail="Unsupported document format")

    document_id = f"doc_{uuid.uuid4().hex}"
    target_dir = (
        DOCUMENT_ROOT
        / _safe_segment(machine["client_id"])
        / _safe_segment(machine_id)
        / document_id
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / "original"
    tmp_name: str | None = None
    size = 0
    digest = hashlib.sha256()
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=target_dir)
        with os.fdopen(fd, "wb") as output:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 250 MB limit")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise HTTPException(status_code=422, detail="Empty files are not supported")
        _validate_content(Path(tmp_name), extension)
        os.replace(tmp_name, final_path)
        tmp_name = None

        timestamp = now_iso()
        category_id = hashlib.sha1(f"{machine['slug']}|{category}".encode()).hexdigest()[:16]
        job_id = f"ingest_{uuid.uuid4().hex}"
        proto_db.write(
            """
            MATCH (m:Machine {slug: $slug})
            MERGE (cat:DocumentCategory {id: $category_id})
            SET cat.name = $category, cat.machine_slug = $slug
            CREATE (d:Document {
              id: $document_id, name: $name, original_name: $name,
              rel_path: $storage_rel_path, storage_rel_path: $storage_rel_path,
              path: $path, kind: $kind, content_type: $content_type,
              size: $size, category: $category,
              source: 'upload', status: 'queued', checksum_sha256: $checksum,
              uploaded_by_id: $actor, uploaded_at: $now, updated_at: $now,
              progress_current: 0, progress_total: 0, retry_count: 0
            })
            CREATE (j:IngestionJob {
              id: $job_id, status: 'queued', phase: 'queued', attempt_count: 0,
              progress_current: 0, progress_total: 0, created_at: $now,
              updated_at: $now, next_attempt_at: $now
            })
            MERGE (m)-[:HAS_CATEGORY]->(cat)
            MERGE (cat)-[:CONTAINS]->(d)
            MERGE (m)-[:HAS_DOCUMENT]->(d)
            CREATE (d)-[:HAS_INGESTION_JOB]->(j)
            CREATE (d)-[:CURRENT_JOB]->(j)
            """,
            {
                "slug": machine["slug"],
                "category_id": category_id,
                "category": category,
                "document_id": document_id,
                "name": original_name,
                "storage_rel_path": str(final_path.relative_to(DOCUMENT_ROOT)),
                "path": str(final_path),
                "kind": kind,
                "content_type": CONTENT_TYPES[extension],
                "size": size,
                "checksum": digest.hexdigest(),
                "actor": user["id"],
                "now": timestamp,
                "job_id": job_id,
            },
        )
    except Exception:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
        if final_path.exists():
            final_path.unlink()
        if target_dir.exists() and not any(target_dir.iterdir()):
            target_dir.rmdir()
        raise
    finally:
        await upload.close()

    _audit(user, "document_uploaded", "document", document_id, original_name)
    return get_document(document_id, user)


DOCUMENT_RETURN = """
RETURN d.id AS id, d.name AS name, d.kind AS kind, d.category AS category,
       d.size AS size, d.source AS source, d.status AS status,
       d.uploaded_by_id AS uploaded_by_id, d.uploaded_at AS uploaded_at,
       d.updated_at AS updated_at, d.error_message AS error_message,
       coalesce(d.phase, j.phase, d.status) AS phase,
       coalesce(d.progress_current, j.progress_current, 0) AS progress_current,
       coalesce(d.progress_total, j.progress_total, 0) AS progress_total,
       coalesce(d.retry_count, 0) AS retry_count,
       j.id AS job_id, j.created_at AS job_created_at,
       coalesce(m.erp_id, 'legacy:' + m.slug) AS machine_erp_id, m.slug AS machine_slug,
       c.erp_id AS client_id
"""


def _with_queue_positions(documents: list[dict]) -> list[dict]:
    for document in documents:
        document["queue_position"] = None
        if document.get("status") != "queued" or not document.get("job_id"):
            document.pop("job_created_at", None)
            document.pop("job_id", None)
            continue
        row = result_single(
            proto_db.query(
                """
                MATCH (j:IngestionJob {status: 'queued'})
                WHERE j.created_at < $created_at
                   OR (j.created_at = $created_at AND j.id <= $job_id)
                RETURN count(j) AS position
                """,
                {
                    "created_at": document.get("job_created_at") or "",
                    "job_id": document["job_id"],
                },
            )
        )
        document["queue_position"] = int((row or {}).get("position") or 1)
        document.pop("job_created_at", None)
        document.pop("job_id", None)
    return documents


def list_documents(machine_id: str, user: dict) -> list[dict]:
    require_knowledge_manager(user)
    _managed_machine(machine_id)
    return _with_queue_positions(result_to_dicts(
        proto_db.query(
            f"""
            MATCH (c:Customer)-[:HAS_MACHINE]->(m:Machine)-[:HAS_DOCUMENT]->(d:Document)
            WHERE m.erp_id = $machine_id OR 'legacy:' + m.slug = $machine_id
            OPTIONAL MATCH (d)-[:CURRENT_JOB]->(j:IngestionJob)
            {DOCUMENT_RETURN}
            ORDER BY coalesce(d.uploaded_at, '') DESC, toLower(d.name)
            """,
            {"machine_id": machine_id},
        )
    ))


def get_document(document_id: str, user: dict) -> dict:
    require_knowledge_manager(user)
    row = result_single(
        proto_db.query(
            f"""
            MATCH (c:Customer)-[:HAS_MACHINE]->(m:Machine)-[:HAS_DOCUMENT]->(d:Document {{id: $id}})
            WHERE coalesce(c.active, false) AND coalesce(m.selected, false)
            OPTIONAL MATCH (d)-[:CURRENT_JOB]->(j:IngestionJob)
            {DOCUMENT_RETURN}
            LIMIT 1
            """,
            {"id": document_id},
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return _with_queue_positions([row])[0]


def retry_document(document_id: str, user: dict) -> dict:
    document = get_document(document_id, user)
    if document.get("status") != "failed":
        raise HTTPException(status_code=409, detail="Only failed documents can be retried")
    if document.get("phase") == "delete_failed":
        raise HTTPException(status_code=409, detail="Retry deletion with the Delete action")
    timestamp = now_iso()
    job_id = f"ingest_{uuid.uuid4().hex}"
    proto_db.write(
        """
        MATCH (d:Document {id: $id})-[old:CURRENT_JOB]->(:IngestionJob)
        DELETE old
        CREATE (j:IngestionJob {
          id: $job_id, status: 'queued', phase: 'queued', attempt_count: 0,
          progress_current: 0, progress_total: 0, created_at: $now,
          updated_at: $now, next_attempt_at: $now, manual_retry: true
        })
        SET d.status = 'queued', d.phase = 'queued', d.error_message = NULL,
            d.progress_current = 0, d.progress_total = 0, d.retry_count = 0,
            d.updated_at = $now
        CREATE (d)-[:HAS_INGESTION_JOB]->(j)
        CREATE (d)-[:CURRENT_JOB]->(j)
        """,
        {"id": document_id, "job_id": job_id, "now": timestamp},
    )
    _audit(user, "document_retried", "document", document_id)
    return get_document(document_id, user)


def _safe_existing_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        from proto import resolve_source

        candidate = Path(resolve_source(raw))
    path = candidate.resolve()
    root = APP_DATA_ROOT.resolve()
    managed = DOCUMENT_ROOT.resolve()
    if path == root or path == managed:
        return None
    if root not in path.parents and managed not in path.parents:
        return None
    return path


def purge_document(document_id: str) -> bool:
    from proto.ingest import clear_document_payload

    row = result_single(
        proto_db.query(
            "MATCH (d:Document {id: $id}) RETURN d.path AS path, d.category AS category",
            {"id": document_id},
        )
    )
    if not row:
        return True
    source = _safe_existing_path(row.get("path"))
    pages = Path(PROTO_CACHE_DIR) / "pages" / document_id
    try:
        if source and source.is_file():
            source.unlink()
            if DOCUMENT_ROOT.resolve() in source.parents:
                parent = source.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
        if pages.is_dir():
            shutil.rmtree(pages)
    except OSError as exc:
        print(f"Managed document deletion failed for {document_id}: {exc}", flush=True)
        proto_db.write(
            """
            MATCH (d:Document {id: $id})
            SET d.status = 'failed', d.phase = 'delete_failed',
                d.error_message = $error, d.updated_at = $now
            """,
            {
                "id": document_id,
                "error": "The document could not be permanently removed. Try Delete again.",
                "now": now_iso(),
            },
        )
        return False

    clear_document_payload(document_id)
    proto_db.write(
        "MATCH (d:Document {id: $id})-[:HAS_INGESTION_JOB]->(j:IngestionJob) DETACH DELETE j",
        {"id": document_id},
    )
    proto_db.write("MATCH (d:Document {id: $id}) DETACH DELETE d", {"id": document_id})
    proto_db.write(
        """
        MATCH (cat:DocumentCategory)
        WHERE NOT (cat)-[:CONTAINS]->(:Document)
        DETACH DELETE cat
        """
    )
    return True


def delete_document(document_id: str, user: dict) -> bool:
    document = get_document(document_id, user)
    _audit(user, "document_deleted", "document", document_id, document.get("name") or "")
    if document.get("status") == "processing":
        proto_db.write(
            """
            MATCH (d:Document {id: $id})-[:CURRENT_JOB]->(j:IngestionJob)
            SET d.status = 'deleting', d.updated_at = $now,
                j.cancel_requested = true, j.updated_at = $now
            """,
            {"id": document_id, "now": now_iso()},
        )
        return False
    proto_db.write(
        """
        MATCH (d:Document {id: $id})
        SET d.status = 'deleting', d.phase = 'deleting', d.error_message = NULL,
            d.updated_at = $now
        """,
        {"id": document_id, "now": now_iso()},
    )
    return purge_document(document_id)


def queue_health() -> dict:
    row = result_single(
        proto_db.query(
            """
            OPTIONAL MATCH (j:IngestionJob {status: 'queued'})
            WITH count(j) AS queued, min(j.created_at) AS oldest_queued_at
            OPTIONAL MATCH (h:WorkerHeartbeat {name: 'managed-ingest'})
            RETURN queued, oldest_queued_at, h.updated_at AS worker_heartbeat_at,
                   h.worker_id AS worker_id
            """
        )
    ) or {}
    failed = result_single(
        proto_db.query("MATCH (d:Document {status: 'failed'}) RETURN count(d) AS failed")
    ) or {}
    return {**row, "failed": int(failed.get("failed") or 0)}
