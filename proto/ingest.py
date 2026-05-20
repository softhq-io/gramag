"""Vision-enriched ingest for sample machines.

Run: python -m proto.ingest            # sample machines only
     python -m proto.ingest --all       # all machines
     python -m proto.ingest --machine "SMB"
"""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz

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
CACHE.mkdir(parents=True, exist_ok=True)
PAGES_DIR.mkdir(parents=True, exist_ok=True)

RENDER_DPI = 150


def _id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
    return h


def _load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"done": {}}


def _save_checkpoint(cp: dict):
    CHECKPOINT.write_text(json.dumps(cp, indent=2))


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


def _checkpoint_done(done: dict, key: str, f: dict, *, force: bool = False) -> bool:
    if force:
        return False
    entry = done.get(key)
    if not entry:
        return False
    fingerprint = _source_fingerprint(f)
    if not entry.get("fingerprint"):
        entry["fingerprint"] = fingerprint
        return True
    return entry.get("fingerprint") == fingerprint


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
        print(f"      ! render fail: {e}")
        return 0
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
        return 0

    # Batch embed merged content
    embeddings = generate_embeddings_batch([s["merged"] for s in sections])

    written = 0
    for s, emb in zip(sections, embeddings):
        if not emb:
            continue
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


def ingest_machine(m: dict, cp: dict, *, deep: bool = False,
                   max_pdfs: int | None = None, max_images: int | None = None,
                   workers: int = 8, img_workers: int = 8,
                   force: bool = False):
    slug = m["slug"]
    print(f"\n=== {m['folder']} ===")
    upsert_machine(m)

    done = cp["done"].setdefault(slug, {})

    # Pre-create category nodes
    categories = {}
    for cat in m["categories"]:
        categories[cat] = upsert_category(slug, cat)

    # PDFs
    pdfs = m["files"]["pdf"]
    if max_pdfs is not None:
        pdfs = pdfs[:max_pdfs]
    print(f"  PDFs: {len(pdfs)}")
    for i, f in enumerate(pdfs, start=1):
        key = f"pdf::{f['rel']}"
        if _checkpoint_done(done, key, f, force=force):
            continue
        print(f"    [{i}/{len(pdfs)}] {f['name']}")
        cat_id = categories.get(f["category"]) or upsert_category(slug, f["category"])
        t0 = time.time()
        try:
            n = ingest_pdf(slug, cat_id, f, deep=deep, workers=workers)
            done[key] = {
                "sections": n,
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

    # Text configs
    texts = m["files"]["text"]
    print(f"  Configs: {len(texts)}")
    for i, f in enumerate(texts, start=1):
        key = f"txt::{f['rel']}"
        if _checkpoint_done(done, key, f, force=force):
            continue
        print(f"    [{i}/{len(texts)}] {f['name']}")
        cat_id = categories.get(f["category"]) or upsert_category(slug, f["category"])
        try:
            n = ingest_text_config(slug, cat_id, f)
            done[key] = {"ok": n, "ts": time.time(), "fingerprint": _source_fingerprint(f)}
            _save_checkpoint(cp)
        except Exception as e:
            print(f"      ! {e}")

    # Images — parallel caption + embed
    imgs = m["files"]["image"]
    if max_images is not None:
        imgs = imgs[:max_images]
    print(f"  Images: {len(imgs)}")
    pending = [
        (i, f)
        for i, f in enumerate(imgs, start=1)
        if not _checkpoint_done(done, f"img::{f['rel']}", f, force=force)
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
    args = ap.parse_args()

    manifest = _load_manifest()
    cp = _load_checkpoint()

    if args.machine:
        targets = [m for m in manifest["machines"] if m["folder"] == args.machine]
    elif args.all:
        targets = manifest["machines"]
    else:
        targets = [m for m in manifest["machines"] if m["folder"] in SAMPLE_MACHINES]

    print(f"Targets: {len(targets)} machines (machine_workers={args.machine_workers}, "
          f"page_workers={args.workers}, img_workers={args.img_workers})")
    if args.machine_workers > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=args.machine_workers) as pool:
            futs = [
                pool.submit(
                    ingest_machine, m, cp, deep=args.deep,
                    max_pdfs=args.max_pdfs, max_images=args.max_images,
                    workers=args.workers, img_workers=args.img_workers,
                    force=args.force,
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
            ingest_machine(m, cp, deep=args.deep,
                           max_pdfs=args.max_pdfs, max_images=args.max_images,
                           workers=args.workers, img_workers=args.img_workers,
                           force=args.force)

    print("\nDone.")
    print("Stats:")
    for label, count in proto_db.stats()["nodes"].items():
        print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
