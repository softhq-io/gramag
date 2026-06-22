"""Vision-enriched ingest for sample machines.

Run: python -m proto.ingest            # sample machines only
     python -m proto.ingest --all       # all machines
     python -m proto.ingest --machine "SMB"
"""

import argparse
import hashlib
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz

from db_helpers import result_value
from embeddings import generate_embedding, generate_embeddings_batch
from proto import PROTO_CACHE_DIR, PROTO_MANIFEST_PATH, SAMPLE_MACHINES
from proto.db_proto import proto_db
from proto.vision import (
    summarize_config,
    vision_caption_image,
    vision_extract_page,
    with_retry,
)

CACHE = Path(PROTO_CACHE_DIR)
PAGES_DIR = CACHE / "pages"
CHECKPOINT = CACHE / "ingest_checkpoint.json"
STAGE_CHECKPOINT = CACHE / "stage_checkpoint.json"
CHECKPOINT_LOCK = threading.Lock()
CACHE.mkdir(parents=True, exist_ok=True)
PAGES_DIR.mkdir(parents=True, exist_ok=True)

RENDER_DPI = 150
SUPPORTED_KINDS = {"pdf", "text", "image"}


class PDFIngestError(RuntimeError):
    """Raised when a PDF cannot be fully represented in the graph."""


def _id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return h


def _load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"done": {}}


def _save_checkpoint(cp: dict, path: Path | None = None):
    checkpoint = path or CHECKPOINT
    content = json.dumps(cp, indent=2)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with CHECKPOINT_LOCK:
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{checkpoint.name}.",
                suffix=".tmp",
                dir=checkpoint.parent,
                text=True,
            )
            with os.fdopen(fd, "w") as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, checkpoint)
            try:
                dir_fd = os.open(checkpoint.parent, os.O_RDONLY)
            except OSError:
                dir_fd = None
            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)


def parse_kinds(value: str) -> set[str]:
    kinds = {part.strip().lower() for part in value.split(",") if part.strip()}
    unknown = kinds - SUPPORTED_KINDS
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unsupported ingest kind(s): {', '.join(sorted(unknown))}; "
            f"expected any of {', '.join(sorted(SUPPORTED_KINDS))}"
        )
    if not kinds:
        raise argparse.ArgumentTypeError("at least one ingest kind is required")
    return kinds


def _load_checkpoint_file(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"done": {}}


def _load_manifest() -> dict:
    return json.loads(Path(PROTO_MANIFEST_PATH).read_text())


def _source_fingerprint(f: dict) -> str:
    """Fingerprint source content enough to notice SharePoint refreshes."""
    size = f.get("size")
    mtime = f.get("mtime")
    if size is None or mtime is None:
        from proto import resolve_source
        try:
            stat = Path(resolve_source(f["path"])).stat()
            size = stat.st_size
            mtime = int(stat.st_mtime)
        except OSError:
            size = size or 0
            mtime = mtime or 0
    return f"{f.get('rel', f.get('path', ''))}|{size}|{int(mtime or 0)}"


def _document_payload_count(doc_id: str, kind: str) -> int:
    rel, label = {
        "pdf": ("HAS_SECTION", "ManualSection"),
        "text": ("HAS_CONFIG", "ConfigFile"),
        "image": ("HAS_IMAGE", "ImageAsset"),
    }[kind]
    docs = result_value(
        proto_db.query("MATCH (d:Document {id: $doc_id}) RETURN count(d) AS c", {"doc_id": doc_id}),
        "c",
        0,
    )
    if not docs:
        return 0
    return int(result_value(
        proto_db.query(
            f"""
            MATCH (d:Document {{id: $doc_id}})-[:{rel}]->(n:{label})
            RETURN count(n) AS c
            """,
            {"doc_id": doc_id},
        ),
        "c",
        0,
    ))


def _document_payload_present(doc_id: str, kind: str) -> bool:
    return bool(_document_payload_count(doc_id, kind))


def _pdf_page_count(f: dict) -> int | None:
    try:
        from proto import resolve_source
        doc = fitz.open(Path(resolve_source(f["path"])))
        try:
            return int(doc.page_count)
        finally:
            doc.close()
    except Exception:
        return None


def _checkpoint_done(
    done: dict,
    key: str,
    f: dict,
    *,
    force: bool = False,
    machine_slug: str | None = None,
    kind: str | None = None,
) -> bool:
    if force:
        return False
    entry = done.get(key)
    if not entry:
        return False
    if entry.get("err"):
        return False
    fingerprint = _source_fingerprint(f)
    if not entry.get("fingerprint"):
        entry["fingerprint"] = fingerprint
        return True
    if entry.get("fingerprint") != fingerprint:
        return False
    if machine_slug and kind:
        doc_id = _id(machine_slug, f["rel"])
        if kind == "pdf":
            expected = entry.get("expected_sections")
            if expected is None:
                expected = _pdf_page_count(f)
            if expected is not None:
                return _document_payload_count(doc_id, kind) >= int(expected)
        return _document_payload_present(doc_id, kind)
    return True


def clear_document_payload(doc_id: str):
    """Remove generated child nodes before reprocessing an updated document."""
    for rel, label in (
        ("HAS_SECTION", "ManualSection"),
        ("HAS_CONFIG", "ConfigFile"),
        ("HAS_IMAGE", "ImageAsset"),
    ):
        proto_db.write(
            f"""
            MATCH (d:Document {{id: $doc_id}})-[r:{rel}]->(n:{label})
            DELETE r, n
            """,
            {"doc_id": doc_id},
        )


def upsert_machine(m: dict) -> str:
    proto_db.write(
        """
        MERGE (n:Machine {slug: $slug})
        SET n.folder = $folder, n.type = $type, n.model = $model,
            n.serial = $serial, n.raw = $raw, n.path = $path,
            n.rel_path = $rel_path, n.customer = $customer
        """,
        {
            "slug": m["slug"], "folder": m["folder"], "type": m["type"],
            "model": m.get("model"), "serial": m.get("serial"),
            "raw": m["raw"], "path": m["path"],
            "rel_path": m.get("rel_path"), "customer": m.get("customer"),
        },
    )
    if m.get("customer"):
        customer_id = _id(m["customer"])
        proto_db.write(
            """
            MATCH (m:Machine {slug: $slug})
            MERGE (c:Customer {id: $customer_id})
            SET c.name = $customer
            MERGE (c)-[:HAS_MACHINE]->(m)
            """,
            {"slug": m["slug"], "customer_id": customer_id, "customer": m["customer"]},
        )
    return m["slug"]


def upsert_category(machine_slug: str, category: str) -> str:
    cat_id = _id(machine_slug, category)
    proto_db.write(
        """
        MATCH (m:Machine {slug: $slug})
        MERGE (c:DocumentCategory {id: $cat_id})
        SET c.name = $name, c.machine_slug = $slug
        MERGE (m)-[:HAS_CATEGORY]->(c)
        """,
        {"slug": machine_slug, "cat_id": cat_id, "name": category},
    )
    return cat_id


def upsert_document(machine_slug: str, cat_id: str, f: dict, kind: str) -> str:
    doc_id = _id(machine_slug, f["rel"])
    proto_db.write(
        """
        MATCH (m:Machine {slug: $slug})
        MATCH (c:DocumentCategory {id: $cat_id})
        MERGE (d:Document {id: $doc_id})
        SET d.name = $name, d.rel_path = $rel, d.path = $path,
            d.kind = $kind, d.size = $size, d.category = $category
        MERGE (m)-[:HAS_DOCUMENT]->(d)
        MERGE (c)-[:CONTAINS]->(d)
        """,
        {
            "slug": machine_slug, "cat_id": cat_id, "doc_id": doc_id,
            "name": f["name"], "rel": f["rel"], "path": f["path"],
            "kind": kind, "size": f["size"], "category": f["category"],
        },
    )
    return doc_id


def render_pdf_pages(pdf_path: Path, doc_id: str) -> list[dict]:
    """Render each page to PNG, return list of {page, text, png_path}.

    png_path is stored as RELATIVE to PROTO_CACHE_DIR for portability.
    """
    out = []
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc, start=1):
        rel = Path("pages") / doc_id / f"p{i:04d}.png"
        abs_path = Path(PROTO_CACHE_DIR) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        pix = page.get_pixmap(dpi=RENDER_DPI)
        pix.save(abs_path)
        text = page.get_text().strip()
        out.append({"page": i, "text": text, "png_path": str(rel)})
    doc.close()
    return out


def _vision_for_page(p: dict, deep: bool, skip_vision_if_short: bool) -> tuple[int, str]:
    text = p["text"]
    if skip_vision_if_short and 40 < len(text) < 80:
        return p["page"], ""
    from proto import resolve_cache
    abs_png = resolve_cache(p["png_path"])
    try:
        return p["page"], with_retry(vision_extract_page, abs_png, deep=deep)
    except Exception as e:
        print(f"      ! vision fail p{p['page']}: {e}")
        return p["page"], ""


def ingest_pdf(machine_slug: str, cat_id: str, f: dict, *, deep: bool = False,
               skip_vision_if_short: bool = True, workers: int = 8) -> int:
    doc_id = upsert_document(machine_slug, cat_id, f, "pdf")
    from proto import resolve_source
    pdf_path = Path(resolve_source(f["path"]))
    try:
        pages = render_pdf_pages(pdf_path, doc_id)
    except Exception as e:
        raise PDFIngestError(f"render failed: {e}") from e
    clear_document_payload(doc_id)

    vision_map: dict[int, str] = {}
    if workers > 1 and len(pages) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_vision_for_page, p, deep, skip_vision_if_short)
                for p in pages
            ]
            for fut in as_completed(futures):
                page_no, desc = fut.result()
                vision_map[page_no] = desc
    else:
        for p in pages:
            page_no, desc = _vision_for_page(p, deep, skip_vision_if_short)
            vision_map[page_no] = desc

    sections = []
    for p in pages:
        text = p["text"]
        vision_desc = vision_map.get(p["page"], "")
        merged = f"PAGE TEXT:\n{text}\n\nVISION ANALYSIS:\n{vision_desc}".strip()
        section_id = _id(doc_id, f"p{p['page']}")
        sections.append({
            "id": section_id, "doc_id": doc_id, "page": p["page"],
            "text": text, "vision_desc": vision_desc, "merged": merged,
            "png_path": p["png_path"],
        })

    if not sections:
        raise PDFIngestError("PDF has no sections to ingest")

    # Batch embed merged content
    embeddings = generate_embeddings_batch([s["merged"] for s in sections])
    if len(embeddings) != len(sections):
        raise PDFIngestError(
            f"embedding count mismatch: expected {len(sections)}, got {len(embeddings)}"
        )
    missing_embeddings = [s["page"] for s, emb in zip(sections, embeddings) if not emb]
    if missing_embeddings:
        pages_preview = ", ".join(str(page) for page in missing_embeddings[:5])
        raise PDFIngestError(
            f"missing embeddings for {len(missing_embeddings)} section(s), "
            f"first page(s): {pages_preview}"
        )

    written = 0
    for s, emb in zip(sections, embeddings):
        try:
            proto_db.write(
                """
                MATCH (d:Document {id: $doc_id})
                MERGE (s:ManualSection {id: $id})
                SET s.document_id = $doc_id, s.page = $page,
                    s.text = $text, s.vision_desc = $vision, s.merged = $merged,
                    s.png_path = $png, s.embedding = vecf32($emb)
                MERGE (d)-[:HAS_SECTION]->(s)
                """,
                {
                    "doc_id": s["doc_id"], "id": s["id"], "page": s["page"],
                    "text": s["text"], "vision": s["vision_desc"],
                    "merged": s["merged"], "png": s["png_path"], "emb": emb,
                },
            )
            written += 1
        except Exception as e:
            print(f"      ! section write fail p{s['page']}: {str(e)[:120]}")
            raise PDFIngestError(f"section write failed p{s['page']}: {e}") from e

    stored = _document_payload_count(doc_id, "pdf")
    if stored < len(sections):
        raise PDFIngestError(
            f"graph verification failed: expected {len(sections)} sections, found {stored}"
        )
    return written


def ingest_text_config(machine_slug: str, cat_id: str, f: dict) -> int:
    doc_id = upsert_document(machine_slug, cat_id, f, "text")
    try:
        content = Path(f["path"]).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"      ! read fail: {e}")
        return 0

    try:
        summary = with_retry(summarize_config, f["name"], content)
    except Exception as e:
        print(f"      ! summary fail: {e}")
        summary = ""

    merged = f"FILE: {f['name']}\n\nSUMMARY:\n{summary}\n\nCONTENT:\n{content[:4000]}"
    emb = generate_embedding(merged)
    cfg_id = _id(doc_id, "config")
    clear_document_payload(doc_id)
    proto_db.write(
        """
        MATCH (d:Document {id: $doc_id})
        MERGE (c:ConfigFile {id: $id})
        SET c.name = $name, c.content = $content, c.summary = $summary,
            c.rel_path = $rel, c.embedding = vecf32($emb)
        MERGE (d)-[:HAS_CONFIG]->(c)
        """,
        {
            "doc_id": doc_id, "id": cfg_id, "name": f["name"],
            "content": content[:20000], "summary": summary,
            "rel": f["rel"], "emb": emb,
        },
    )
    return 1


def ingest_image_asset(machine_slug: str, cat_id: str, f: dict, *, deep: bool = False) -> int:
    doc_id = upsert_document(machine_slug, cat_id, f, "image")
    try:
        caption = with_retry(vision_caption_image, f["path"], deep=deep)
    except Exception as e:
        print(f"      ! vision fail: {e}")
        caption = ""

    merged = f"IMAGE: {f['name']} (category: {f['category']})\n\n{caption}"
    emb = generate_embedding(merged)
    img_id = _id(doc_id, "image")
    clear_document_payload(doc_id)
    proto_db.write(
        """
        MATCH (d:Document {id: $doc_id})
        MERGE (i:ImageAsset {id: $id})
        SET i.name = $name, i.caption = $caption, i.path = $path,
            i.category = $category, i.rel_path = $rel,
            i.embedding = vecf32($emb)
        MERGE (d)-[:HAS_IMAGE]->(i)
        """,
        {
            "doc_id": doc_id, "id": img_id, "name": f["name"],
            "caption": caption, "path": f["path"],
            "category": f["category"], "rel": f["rel"], "emb": emb,
        },
    )
    return 1


def _machine_record(m: dict) -> dict:
    return {
        "slug": m["slug"],
        "folder": m["folder"],
        "type": m["type"],
        "model": m.get("model"),
        "serial": m.get("serial"),
        "raw": m["raw"],
        "path": m["path"],
        "rel_path": m.get("rel_path"),
        "customer": m.get("customer"),
    }


def _write_jsonl_record(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as out:
        out.write(line)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def _stage_base_record(m: dict, cat_id: str, f: dict, kind: str) -> dict:
    machine_slug = m["slug"]
    return {
        "schema": 1,
        "machine": _machine_record(m),
        "cat_id": cat_id,
        "category": f["category"],
        "doc_id": _id(machine_slug, f["rel"]),
        "kind": kind,
        "file": {
            "name": f["name"],
            "rel": f["rel"],
            "path": f["path"],
            "size": f.get("size"),
            "category": f["category"],
        },
        "fingerprint": _source_fingerprint(f),
    }


def stage_pdf_records(m: dict, cat_id: str, f: dict, output_path: Path, *,
                      deep: bool = False, skip_vision_if_short: bool = True,
                      workers: int = 8) -> int:
    base = _stage_base_record(m, cat_id, f, "pdf")
    doc_id = base["doc_id"]
    from proto import resolve_source
    pages = render_pdf_pages(Path(resolve_source(f["path"])), doc_id)
    if not pages:
        raise PDFIngestError("PDF has no sections to stage")

    vision_map: dict[int, str] = {}
    if workers > 1 and len(pages) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_vision_for_page, p, deep, skip_vision_if_short)
                for p in pages
            ]
            for fut in as_completed(futures):
                page_no, desc = fut.result()
                vision_map[page_no] = desc
    else:
        for p in pages:
            page_no, desc = _vision_for_page(p, deep, skip_vision_if_short)
            vision_map[page_no] = desc

    sections = []
    for p in pages:
        text = p["text"]
        vision_desc = vision_map.get(p["page"], "")
        merged = f"PAGE TEXT:\n{text}\n\nVISION ANALYSIS:\n{vision_desc}".strip()
        sections.append({
            "id": _id(doc_id, f"p{p['page']}"),
            "page": p["page"],
            "text": text,
            "vision_desc": vision_desc,
            "merged": merged,
            "png_path": p["png_path"],
        })

    embeddings = generate_embeddings_batch([s["merged"] for s in sections])
    if len(embeddings) != len(sections):
        raise PDFIngestError(
            f"embedding count mismatch: expected {len(sections)}, got {len(embeddings)}"
        )
    missing_embeddings = [s["page"] for s, emb in zip(sections, embeddings) if not emb]
    if missing_embeddings:
        pages_preview = ", ".join(str(page) for page in missing_embeddings[:5])
        raise PDFIngestError(
            f"missing embeddings for {len(missing_embeddings)} section(s), "
            f"first page(s): {pages_preview}"
        )

    _write_jsonl_record(output_path, {
        **base,
        "record": "document",
        "expected_sections": len(sections),
        "ts": time.time(),
    })
    for section, emb in zip(sections, embeddings):
        _write_jsonl_record(output_path, {
            **base,
            "record": "manual_section",
            "section": section,
            "embedding": emb,
            "ts": time.time(),
        })
    return len(sections)


def stage_text_record(m: dict, cat_id: str, f: dict, output_path: Path) -> int:
    base = _stage_base_record(m, cat_id, f, "text")
    content = Path(f["path"]).read_text(encoding="utf-8", errors="replace")
    try:
        summary = with_retry(summarize_config, f["name"], content)
    except Exception as e:
        print(f"      ! summary fail: {e}")
        summary = ""
    merged = f"FILE: {f['name']}\n\nSUMMARY:\n{summary}\n\nCONTENT:\n{content[:4000]}"
    emb = generate_embedding(merged)
    if not emb:
        raise RuntimeError("missing text embedding")
    _write_jsonl_record(output_path, {
        **base,
        "record": "config",
        "config": {
            "id": _id(base["doc_id"], "config"),
            "name": f["name"],
            "content": content[:20000],
            "summary": summary,
            "rel_path": f["rel"],
        },
        "embedding": emb,
        "ts": time.time(),
    })
    return 1


def stage_image_record(m: dict, cat_id: str, f: dict, output_path: Path, *, deep: bool = False) -> int:
    base = _stage_base_record(m, cat_id, f, "image")
    try:
        caption = with_retry(vision_caption_image, f["path"], deep=deep)
    except Exception as e:
        print(f"      ! vision fail: {e}")
        caption = ""
    merged = f"IMAGE: {f['name']} (category: {f['category']})\n\n{caption}"
    emb = generate_embedding(merged)
    if not emb:
        raise RuntimeError("missing image embedding")
    _write_jsonl_record(output_path, {
        **base,
        "record": "image",
        "image": {
            "id": _id(base["doc_id"], "image"),
            "name": f["name"],
            "caption": caption,
            "path": f["path"],
            "category": f["category"],
            "rel_path": f["rel"],
        },
        "embedding": emb,
        "ts": time.time(),
    })
    return 1


def stage_machine(m: dict, cp: dict, output_dir: Path, *, deep: bool = False,
                  max_pdfs: int | None = None, max_images: int | None = None,
                  workers: int = 8, img_workers: int = 8,
                  force: bool = False, kinds: set[str] | None = None):
    kinds = kinds or SUPPORTED_KINDS
    slug = m["slug"]
    output_path = output_dir / f"{slug}.jsonl"
    done = cp["done"].setdefault(slug, {})
    categories = {cat: _id(slug, cat) for cat in m["categories"]}
    print(f"\n=== stage {m['folder']} -> {output_path} ===")

    if "pdf" in kinds:
        pdfs = m["files"]["pdf"]
        if max_pdfs is not None:
            pdfs = pdfs[:max_pdfs]
        print(f"  PDFs: {len(pdfs)}")
        for i, f in enumerate(pdfs, start=1):
            key = f"stage::pdf::{f['rel']}"
            if _checkpoint_done(done, key, f, force=force):
                continue
            print(f"    [{i}/{len(pdfs)}] {f['name']}")
            t0 = time.time()
            try:
                n = stage_pdf_records(
                    m, categories.get(f["category"]) or _id(slug, f["category"]),
                    f, output_path, deep=deep, workers=workers,
                )
                done[key] = {
                    "sections": n,
                    "expected_sections": n,
                    "ts": time.time(),
                    "fingerprint": _source_fingerprint(f),
                }
                _save_checkpoint(cp, STAGE_CHECKPOINT)
                print(f"      -> staged {n} sections in {time.time() - t0:.1f}s")
            except Exception as e:
                print(f"      ! pdf stage failed: {str(e)[:150]}")
                done[key] = {
                    "sections": 0,
                    "ts": time.time(),
                    "fingerprint": _source_fingerprint(f),
                    "err": str(e)[:200],
                }
                _save_checkpoint(cp, STAGE_CHECKPOINT)

    if "text" in kinds:
        texts = m["files"]["text"]
        print(f"  Configs: {len(texts)}")
        for i, f in enumerate(texts, start=1):
            key = f"stage::txt::{f['rel']}"
            if _checkpoint_done(done, key, f, force=force):
                continue
            print(f"    [{i}/{len(texts)}] {f['name']}")
            try:
                n = stage_text_record(
                    m, categories.get(f["category"]) or _id(slug, f["category"]),
                    f, output_path,
                )
                done[key] = {"ok": n, "ts": time.time(), "fingerprint": _source_fingerprint(f)}
                _save_checkpoint(cp, STAGE_CHECKPOINT)
            except Exception as e:
                print(f"      ! text stage failed: {e}")
                done[key] = {"ok": 0, "ts": time.time(), "fingerprint": _source_fingerprint(f), "err": str(e)[:200]}
                _save_checkpoint(cp, STAGE_CHECKPOINT)

    if "image" in kinds:
        imgs = m["files"]["image"]
        if max_images is not None:
            imgs = imgs[:max_images]
        print(f"  Images: {len(imgs)}")
        for i, f in enumerate(imgs, start=1):
            key = f"stage::img::{f['rel']}"
            if _checkpoint_done(done, key, f, force=force):
                continue
            print(f"    [{i}/{len(imgs)}] {f['name']}")
            try:
                n = stage_image_record(
                    m, categories.get(f["category"]) or _id(slug, f["category"]),
                    f, output_path, deep=deep,
                )
                done[key] = {"ok": n, "ts": time.time(), "fingerprint": _source_fingerprint(f)}
                _save_checkpoint(cp, STAGE_CHECKPOINT)
            except Exception as e:
                print(f"      ! image stage failed: {e}")
                done[key] = {"ok": 0, "ts": time.time(), "fingerprint": _source_fingerprint(f), "err": str(e)[:200]}
                _save_checkpoint(cp, STAGE_CHECKPOINT)


def _import_done(cp: dict, key: str, fingerprint: str | None = None) -> bool:
    entry = cp["done"].get(key)
    if not entry or entry.get("err"):
        return False
    if fingerprint and entry.get("fingerprint") != fingerprint:
        return False
    return True


def _mark_import_done(cp: dict, checkpoint: Path, key: str, record: dict, extra: dict | None = None):
    cp["done"][key] = {
        "ts": time.time(),
        "fingerprint": record.get("fingerprint"),
        **(extra or {}),
    }
    _save_checkpoint(cp, checkpoint)


def import_record(record: dict, known_docs: set[str] | None = None):
    machine = record["machine"]
    f = record["file"]
    kind = record["kind"]
    doc_id = record["doc_id"]
    known_docs = known_docs if known_docs is not None else set()
    if record["record"] == "document":
        upsert_machine(machine)
        cat_id = upsert_category(machine["slug"], record["category"])
        upsert_document(machine["slug"], cat_id, f, kind)
        clear_document_payload(doc_id)
        known_docs.add(doc_id)
        return
    if record["record"] == "manual_section":
        if doc_id not in known_docs:
            upsert_machine(machine)
            cat_id = upsert_category(machine["slug"], record["category"])
            upsert_document(machine["slug"], cat_id, f, kind)
            known_docs.add(doc_id)
        s = record["section"]
        proto_db.write(
            """
            MATCH (d:Document {id: $doc_id})
            MERGE (s:ManualSection {id: $id})
            SET s.document_id = $doc_id, s.page = $page,
                s.text = $text, s.vision_desc = $vision, s.merged = $merged,
                s.png_path = $png, s.embedding = vecf32($emb)
            MERGE (d)-[:HAS_SECTION]->(s)
            """,
            {
                "doc_id": doc_id, "id": s["id"], "page": s["page"],
                "text": s["text"], "vision": s["vision_desc"],
                "merged": s["merged"], "png": s["png_path"],
                "emb": record["embedding"],
            },
        )
        return
    if record["record"] == "config":
        upsert_machine(machine)
        cat_id = upsert_category(machine["slug"], record["category"])
        upsert_document(machine["slug"], cat_id, f, kind)
        c = record["config"]
        proto_db.write(
            """
            MATCH (d:Document {id: $doc_id})
            MERGE (c:ConfigFile {id: $id})
            SET c.name = $name, c.content = $content, c.summary = $summary,
                c.rel_path = $rel, c.embedding = vecf32($emb)
            MERGE (d)-[:HAS_CONFIG]->(c)
            """,
            {
                "doc_id": doc_id, "id": c["id"], "name": c["name"],
                "content": c["content"], "summary": c["summary"],
                "rel": c["rel_path"], "emb": record["embedding"],
            },
        )
        return
    if record["record"] == "image":
        upsert_machine(machine)
        cat_id = upsert_category(machine["slug"], record["category"])
        upsert_document(machine["slug"], cat_id, f, kind)
        i = record["image"]
        proto_db.write(
            """
            MATCH (d:Document {id: $doc_id})
            MERGE (i:ImageAsset {id: $id})
            SET i.name = $name, i.caption = $caption, i.path = $path,
                i.category = $category, i.rel_path = $rel,
                i.embedding = vecf32($emb)
            MERGE (d)-[:HAS_IMAGE]->(i)
            """,
            {
                "doc_id": doc_id, "id": i["id"], "name": i["name"],
                "caption": i["caption"], "path": i["path"],
                "category": i["category"], "rel": i["rel_path"],
                "emb": record["embedding"],
            },
        )
        return
    raise RuntimeError(f"Unsupported staged record type: {record['record']}")


def import_staged_records(output_dir: Path, checkpoint: Path, *, sleep_seconds: float = 0.0):
    cp = _load_checkpoint_file(checkpoint)
    files = sorted(output_dir.glob("*.jsonl"))
    print(f"Importing staged Proto records from {output_dir} ({len(files)} files)")
    imported = 0
    known_docs: set[str] = set()
    for path in files:
        print(f"  {path.name}")
        with path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                key = f"{path.name}:{line_no}:{record['record']}:{record['doc_id']}"
                if record["record"] == "manual_section":
                    key += f":{record['section']['id']}"
                elif record["record"] == "config":
                    key += f":{record['config']['id']}"
                elif record["record"] == "image":
                    key += f":{record['image']['id']}"
                if _import_done(cp, key, record.get("fingerprint")):
                    if record["record"] == "document":
                        known_docs.add(record["doc_id"])
                    continue
                try:
                    import_record(record, known_docs)
                    _mark_import_done(cp, checkpoint, key, record)
                    imported += 1
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                except Exception as e:
                    cp["done"][key] = {
                        "ts": time.time(),
                        "fingerprint": record.get("fingerprint"),
                        "err": str(e)[:300],
                    }
                    _save_checkpoint(cp, checkpoint)
                    print(f"    ! import failed {path.name}:{line_no}: {str(e)[:180]}")
                    raise
    print(f"Imported {imported} staged records")


def ingest_machine(m: dict, cp: dict, *, deep: bool = False,
                   max_pdfs: int | None = None, max_images: int | None = None,
                   workers: int = 8, img_workers: int = 8,
                   force: bool = False, kinds: set[str] | None = None):
    kinds = kinds or SUPPORTED_KINDS
    slug = m["slug"]
    print(f"\n=== {m['folder']} ===")
    upsert_machine(m)

    done = cp["done"].setdefault(slug, {})

    # Pre-create category nodes
    categories = {}
    for cat in m["categories"]:
        categories[cat] = upsert_category(slug, cat)

    if "pdf" in kinds:
        pdfs = m["files"]["pdf"]
        if max_pdfs is not None:
            pdfs = pdfs[:max_pdfs]
        print(f"  PDFs: {len(pdfs)}")
        for i, f in enumerate(pdfs, start=1):
            key = f"pdf::{f['rel']}"
            if _checkpoint_done(done, key, f, force=force, machine_slug=slug, kind="pdf"):
                continue
            print(f"    [{i}/{len(pdfs)}] {f['name']}")
            cat_id = categories.get(f["category"]) or upsert_category(slug, f["category"])
            t0 = time.time()
            try:
                n = ingest_pdf(slug, cat_id, f, deep=deep, workers=workers)
                done[key] = {
                    "sections": n,
                    "expected_sections": n,
                    "ts": time.time(),
                    "fingerprint": _source_fingerprint(f),
                }
                _save_checkpoint(cp)
                print(f"      -> {n} sections in {time.time() - t0:.1f}s")
            except Exception as e:
                print(f"      ! pdf failed: {str(e)[:150]}")
                done[key] = {
                    "sections": 0,
                    "ts": time.time(),
                    "fingerprint": _source_fingerprint(f),
                    "err": str(e)[:200],
                }
                _save_checkpoint(cp)

    if "text" in kinds:
        texts = m["files"]["text"]
        print(f"  Configs: {len(texts)}")
        for i, f in enumerate(texts, start=1):
            key = f"txt::{f['rel']}"
            if _checkpoint_done(done, key, f, force=force, machine_slug=slug, kind="text"):
                continue
            print(f"    [{i}/{len(texts)}] {f['name']}")
            cat_id = categories.get(f["category"]) or upsert_category(slug, f["category"])
            try:
                n = ingest_text_config(slug, cat_id, f)
                done[key] = {"ok": n, "ts": time.time(), "fingerprint": _source_fingerprint(f)}
                _save_checkpoint(cp)
            except Exception as e:
                print(f"      ! {e}")

    if "image" in kinds:
        imgs = m["files"]["image"]
        if max_images is not None:
            imgs = imgs[:max_images]
        print(f"  Images: {len(imgs)}")
        pending = [
            (i, f)
            for i, f in enumerate(imgs, start=1)
            if not _checkpoint_done(done, f"img::{f['rel']}", f, force=force, machine_slug=slug, kind="image")
        ]
        if pending:
            def _do_img(i, f):
                cat = categories.get(f["category"]) or upsert_category(slug, f["category"])
                try:
                    n = ingest_image_asset(slug, cat, f, deep=deep)
                    return (f["rel"], n, None)
                except Exception as e:
                    return (f["rel"], 0, str(e))

            with ThreadPoolExecutor(max_workers=img_workers) as pool:
                futures = {pool.submit(_do_img, i, f): (i, f) for i, f in pending}
                for fut in as_completed(futures):
                    i, f = futures[fut]
                    rel, n, err = fut.result()
                    key = f"img::{rel}"
                    if err:
                        print(f"    [{i}/{len(imgs)}] {f['name']}  ! {err}")
                    else:
                        print(f"    [{i}/{len(imgs)}] {f['name']}  ok")
                    done[key] = {
                        "ok": n,
                        "ts": time.time(),
                        "fingerprint": _source_fingerprint(f),
                        **({"err": err} if err else {}),
                    }
                    _save_checkpoint(cp)

    _save_checkpoint(cp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Ingest all 14 machines")
    ap.add_argument("--machine", type=str, help="Ingest a single machine by folder name")
    ap.add_argument("--deep", action="store_true", help="Use Gemini 3 Pro for vision")
    ap.add_argument("--max-pdfs", type=int, default=None, help="Cap PDFs per machine")
    ap.add_argument("--max-images", type=int, default=None, help="Cap images per machine")
    ap.add_argument("--workers", type=int, default=32, help="Parallel vision calls per PDF")
    ap.add_argument("--img-workers", type=int, default=24, help="Parallel image captions")
    ap.add_argument("--machine-workers", type=int, default=1, help="Parallel machines")
    ap.add_argument("--force", action="store_true", help="Reprocess files even when checkpoint fingerprints match")
    ap.add_argument("--kinds", type=parse_kinds, default=SUPPORTED_KINDS, help="Comma-separated kinds to ingest: pdf,text,image")
    ap.add_argument("--stage-output-dir", type=Path, default=None, help="Write extracted records as durable JSONL instead of writing to FalkorDB")
    ap.add_argument("--import-output-dir", type=Path, default=None, help="Import staged JSONL records into FalkorDB as a single writer")
    ap.add_argument("--import-checkpoint", type=Path, default=None, help="Checkpoint path for staged JSONL import")
    ap.add_argument("--import-sleep", type=float, default=0.0, help="Seconds to sleep after each imported staged record")
    args = ap.parse_args()

    if args.import_output_dir:
        checkpoint = args.import_checkpoint or (args.import_output_dir / "import_checkpoint.json")
        import_staged_records(args.import_output_dir, checkpoint, sleep_seconds=args.import_sleep)
        print("\nDone.")
        print("Stats:")
        for label, count in proto_db.stats()["nodes"].items():
            print(f"  {label}: {count}")
        return

    manifest = _load_manifest()
    cp = _load_checkpoint_file(STAGE_CHECKPOINT) if args.stage_output_dir else _load_checkpoint()

    if args.machine:
        targets = [m for m in manifest["machines"] if m["folder"] == args.machine]
    elif args.all:
        targets = manifest["machines"]
    else:
        targets = [m for m in manifest["machines"] if m["folder"] in SAMPLE_MACHINES]

    mode = "stage" if args.stage_output_dir else "direct"
    print(f"Targets: {len(targets)} machines (mode={mode}, kinds={','.join(sorted(args.kinds))}, "
          f"machine_workers={args.machine_workers}, page_workers={args.workers}, "
          f"img_workers={args.img_workers})")
    if args.stage_output_dir:
        args.stage_output_dir.mkdir(parents=True, exist_ok=True)
    if args.machine_workers > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=args.machine_workers) as pool:
            if args.stage_output_dir:
                futs = [
                    pool.submit(
                        stage_machine, m, cp, args.stage_output_dir, deep=args.deep,
                        max_pdfs=args.max_pdfs, max_images=args.max_images,
                        workers=args.workers, img_workers=args.img_workers,
                        force=args.force, kinds=args.kinds,
                    )
                    for m in targets
                ]
            else:
                futs = [
                    pool.submit(
                        ingest_machine, m, cp, deep=args.deep,
                        max_pdfs=args.max_pdfs, max_images=args.max_images,
                        workers=args.workers, img_workers=args.img_workers,
                        force=args.force, kinds=args.kinds,
                    )
                    for m in targets
                ]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as e:
                    print(f"  machine failed: {e}")
    else:
        for m in targets:
            if args.stage_output_dir:
                stage_machine(m, cp, args.stage_output_dir, deep=args.deep,
                              max_pdfs=args.max_pdfs, max_images=args.max_images,
                              workers=args.workers, img_workers=args.img_workers,
                              force=args.force, kinds=args.kinds)
            else:
                ingest_machine(m, cp, deep=args.deep,
                               max_pdfs=args.max_pdfs, max_images=args.max_images,
                               workers=args.workers, img_workers=args.img_workers,
                               force=args.force, kinds=args.kinds)

    print("\nDone.")
    if not args.stage_output_dir:
        print("Stats:")
        for label, count in proto_db.stats()["nodes"].items():
            print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
