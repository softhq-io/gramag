"""Gramag Knowledge Graph — Seed PDFs into FalkorDB.

Pipeline per PDF:
1. Extract text (PyMuPDF) -> smart chunk (~1500 chars)
2. Embed each chunk (Gemini embedding-001)
3. LLM structured extraction per chunk -> error codes, troubleshooting, procedures
4. Write to graph: ManualSection, ErrorCode, TroubleshootingEntry
5. Cross-link: Part -> MENTIONED_IN -> ManualSection via manufacturer_nr matching

Checkpoints progress to resume interrupted runs.

Usage:
    python seed_pdfs.py                 # all PDFs
    python seed_pdfs.py --sample 10     # first 10 (one per supplier)
    python seed_pdfs.py --workers 4     # parallelism (default: 4)
"""

import os
import sys
import json
import time
import re
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz  # PyMuPDF
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, PDF_DIR, EXTRACTION_MODEL
from db import db, GraphConnection
from db_helpers import result_to_dicts
from embeddings import generate_embeddings_batch

client = genai.Client(api_key=GEMINI_API_KEY)

CHECKPOINT_FILE = "/Users/piotrzwolinski/projects/gramag/seed_pdfs_checkpoint.json"

# Thread-safe checkpoint
_checkpoint_lock = threading.Lock()
_done: set = set()
_section_count = 0
_count_lock = threading.Lock()


def load_checkpoint() -> set:
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_checkpoint(done: set):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(list(done), f)


def sanitize_text(text: str) -> str:
    """Strip control characters that break FalkorDB's Cypher parser."""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)


def extract_text_by_page(pdf_path: str) -> list[dict]:
    """Extract text from each page."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = sanitize_text(page.get_text().strip())
        if text:
            pages.append({"page_num": i + 1, "text": text})
    doc.close()
    return pages


def smart_chunk(pages: list[dict], max_chars: int = 1500) -> list[dict]:
    """Chunk by page boundaries with smart merging."""
    chunks = []
    buf, buf_pages = "", []

    for page in pages:
        text = page["text"]
        pnum = page["page_num"]

        if buf and len(buf) + len(text) > max_chars:
            chunks.append({"text": buf.strip(), "pages": list(buf_pages)})
            buf, buf_pages = "", []

        if len(text) > max_chars:
            if buf.strip():
                chunks.append({"text": buf.strip(), "pages": list(buf_pages)})
                buf, buf_pages = "", []
            # Split long page at paragraph breaks
            paragraphs = re.split(r'\n\s*\n', text)
            para_buf = ""
            for para in paragraphs:
                if len(para_buf) + len(para) > max_chars:
                    if para_buf.strip():
                        chunks.append({"text": para_buf.strip(), "pages": [pnum]})
                    para_buf = para
                else:
                    para_buf += "\n\n" + para
            if para_buf.strip():
                buf, buf_pages = para_buf, [pnum]
        else:
            buf += "\n\n" + text
            buf_pages.append(pnum)

    if buf.strip():
        chunks.append({"text": buf.strip(), "pages": list(buf_pages)})

    return chunks


EXTRACTION_PROMPT = """Analyze this section of a technical manual for industrial machines (printing, folding, cutting, enveloping, labelling).

Document: {doc_name} (Supplier: {supplier})
Pages: {pages}

Extract structured information. Return valid JSON only. Include only what's actually present:

{{
  "error_codes": [
    {{"code": "E01", "description": "...", "cause": "...", "solution": "..."}}
  ],
  "troubleshooting": [
    {{"symptom": "...", "causes": ["..."], "solutions": ["..."]}}
  ],
  "section_summary": "One paragraph summary"
}}

RULES:
- Extract REAL data only, do not invent
- Keep original language for part numbers and technical terms
- If section is mostly diagrams/images, note in summary

TEXT:
{text}
"""


def extract_structured(chunk_text: str, doc_name: str, supplier: str, pages: list[int]) -> dict:
    """Use LLM to extract structured entities from a chunk."""
    prompt = EXTRACTION_PROMPT.format(
        doc_name=doc_name,
        supplier=supplier,
        pages=f"{pages[0]}-{pages[-1]}" if len(pages) > 1 else str(pages[0]),
        text=chunk_text[:6000],
    )

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=EXTRACTION_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                    http_options={"timeout": 60_000},
                ),
            )
            parsed = json.loads(response.text)
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
            if not isinstance(parsed, dict):
                parsed = {}
            return parsed
        except Exception as e:
            if "429" in str(e) or "quota" in str(e).lower():
                wait = 2 ** (attempt + 1)
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                return {"error": str(e), "section_summary": ""}
    return {"section_summary": ""}


def get_supplier_from_path(path: str) -> str:
    """Extract supplier name from path like .../Lieferanten/Avery/..."""
    if "Lieferanten" in path:
        return path.split("Lieferanten/")[-1].split("/")[0]
    return "Unknown"


def collect_pdfs() -> list[dict]:
    """Walk the Servicedaten directory and collect all PDFs."""
    pdfs = []
    for root, _, files in os.walk(PDF_DIR):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(root, fname)
            supplier = get_supplier_from_path(path)
            pdfs.append({"path": path, "name": fname, "supplier": supplier})
    return pdfs


def process_pdf(pdf_info: dict) -> dict:
    """Process a single PDF: extract, chunk, embed, extract structured, write to graph.

    Returns stats dict. Each thread uses its own DB connection.
    """
    global _section_count

    path = pdf_info["path"]
    name = pdf_info["name"]
    supplier = pdf_info["supplier"]

    # Each thread gets its own DB connection
    local_db = GraphConnection()
    local_db.connect()

    stats = {"sections": 0, "errors": 0, "troubleshooting": 0, "name": name}

    # Ensure Supplier node exists
    local_db.write(
        "MERGE (s:Supplier {name: $name})",
        {"name": supplier},
    )

    # Extract + chunk
    try:
        pages = extract_text_by_page(path)
    except Exception as e:
        print(f"    SKIP {name}: {e}")
        local_db.close()
        return stats
    if not pages:
        local_db.close()
        return stats

    chunks = smart_chunk(pages)
    if not chunks:
        local_db.close()
        return stats

    # Embed all chunks
    print(f"    [{name[:40]}] Embedding {len(chunks)} chunks...", flush=True)
    texts = [c["text"][:2000] for c in chunks]
    embeddings = generate_embeddings_batch(texts)
    print(f"    [{name[:40]}] Embeddings done. Starting LLM extraction...", flush=True)

    # LLM extraction — parallelize across all chunks
    extractions = [None] * len(chunks)
    _extract_done = 0
    _extract_lock = threading.Lock()

    def _extract_chunk(idx):
        nonlocal _extract_done
        result = idx, extract_structured(
            chunks[idx]["text"], name, supplier, chunks[idx]["pages"]
        )
        with _extract_lock:
            _extract_done += 1
            if _extract_done % 20 == 0 or _extract_done == len(chunks):
                print(f"    [{name[:40]}] LLM {_extract_done}/{len(chunks)}", flush=True)
        return result

    extract_indices = list(range(len(chunks)))
    with ThreadPoolExecutor(max_workers=3) as chunk_pool:
        futures = {chunk_pool.submit(_extract_chunk, i): i for i in extract_indices}
        for future in as_completed(futures):
            idx, result = future.result()
            extractions[idx] = result

    # Write to graph
    print(f"    [{name[:40]}] Writing {len(chunks)} sections to graph...", flush=True)
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        if not emb:
            continue

        section_id = re.sub(r'[^a-zA-Z0-9._-]', '_', f"{name}_{i}")
        with _count_lock:
            _section_count += 1
        stats["sections"] += 1
        pages_str = f"{chunk['pages'][0]}-{chunk['pages'][-1]}" if len(chunk['pages']) > 1 else str(chunk['pages'][0])

        extracted = extractions[i] or {}
        summary = extracted.get("section_summary", "")
        error_codes = extracted.get("error_codes", [])
        troubleshooting = extracted.get("troubleshooting", [])

        # Write ManualSection node with embedding
        local_db.write("""
            MERGE (ms:ManualSection {id: $sid})
            SET ms.title = $title, ms.summary = $summary,
                ms.supplier = $supplier, ms.pages = $pages,
                ms.text = $text, ms.embedding = vecf32($embedding)
            WITH ms
            MATCH (s:Supplier {name: $supplier})
            MERGE (ms)-[:FROM_SUPPLIER]->(s)
        """, {
            "sid": section_id,
            "title": f"{name} p.{pages_str}",
            "summary": summary,
            "supplier": supplier,
            "pages": pages_str,
            "text": chunk["text"][:1000],
            "embedding": emb,
        })

        # Write ErrorCode nodes
        for ec in error_codes:
            code = ec.get("code", "").strip()
            if not code:
                continue
            local_db.write("""
                MERGE (ec:ErrorCode {code: $code, supplier: $supplier})
                SET ec.description = $desc, ec.cause = $cause, ec.solution = $solution
                WITH ec
                MATCH (ms:ManualSection {id: $ms_id})
                MERGE (ms)-[:HAS_ERROR]->(ec)
            """, {
                "code": code,
                "supplier": supplier,
                "desc": ec.get("description", ""),
                "cause": ec.get("cause", ""),
                "solution": ec.get("solution", ""),
                "ms_id": section_id,
            })
            stats["errors"] += 1

        # Write TroubleshootingEntry nodes
        for j, ts in enumerate(troubleshooting):
            symptom = (ts.get("symptom") or "").strip()
            if not symptom:
                continue
            ts_id = f"{section_id}_ts{j}"
            local_db.write("""
                MERGE (te:TroubleshootingEntry {id: $tid})
                SET te.symptom = $symptom,
                    te.causes = $causes,
                    te.solutions = $solutions
                WITH te
                MATCH (ms:ManualSection {id: $ms_id})
                MERGE (ms)-[:HAS_TROUBLESHOOTING]->(te)
            """, {
                "tid": ts_id,
                "symptom": symptom,
                "causes": json.dumps(ts.get("causes", []), ensure_ascii=False),
                "solutions": json.dumps(ts.get("solutions", []), ensure_ascii=False),
                "ms_id": section_id,
            })
            stats["troubleshooting"] += 1

    local_db.close()
    return stats


def cross_link_parts():
    """Link Parts to ManualSections where manufacturer numbers are mentioned."""
    print("\n  Cross-linking parts to manual sections...")

    # Get all parts with manufacturer numbers
    result = db.query("""
        MATCH (p:Part)
        WHERE p.manufacturer_nr <> '' AND size(p.manufacturer_nr) > 3
        RETURN p.erp_id AS erp_id, p.manufacturer_nr AS mfr_nr
    """)
    parts = result_to_dicts(result)
    print(f"    Parts with manufacturer numbers: {len(parts)}")

    linked = 0
    for p in parts:
        mfr = p["mfr_nr"]
        # Search ManualSection text for this manufacturer number
        result = db.query("""
            MATCH (ms:ManualSection)
            WHERE ms.text CONTAINS $mfr
            RETURN ms.id AS ms_id
            LIMIT 5
        """, {"mfr": mfr})
        sections = result_to_dicts(result)
        for s in sections:
            db.write("""
                MATCH (p:Part {erp_id: $erp_id})
                MATCH (ms:ManualSection {id: $ms_id})
                MERGE (p)-[:MENTIONED_IN]->(ms)
            """, {"erp_id": p["erp_id"], "ms_id": s["ms_id"]})
            linked += 1

    print(f"    MENTIONED_IN links created: {linked}")


def main():
    parser = argparse.ArgumentParser(description="Seed PDFs into FalkorDB")
    parser.add_argument("--sample", type=int, default=0,
                        help="Process only N PDFs (picks diverse suppliers)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Number of parallel PDF workers (default: 4)")
    args = parser.parse_args()

    global _done, _section_count

    print("=" * 60)
    print("  Gramag Knowledge Graph — PDF Seed")
    print(f"  Model: {EXTRACTION_MODEL}, Workers: {args.workers}")
    if args.sample:
        print(f"  SAMPLE MODE: {args.sample} PDFs")
    print("=" * 60)

    t0 = time.time()
    db.connect()

    pdfs = collect_pdfs()
    print(f"Found {len(pdfs)} PDFs")

    _done = load_checkpoint()
    remaining = [p for p in pdfs if p["path"] not in _done]
    print(f"Already processed: {len(_done)}, remaining: {len(remaining)}")

    # Sample mode: pick diverse PDFs across suppliers
    if args.sample and args.sample < len(remaining):
        by_supplier = {}
        for p in remaining:
            by_supplier.setdefault(p["supplier"], []).append(p)

        sampled = []
        # Round-robin across suppliers
        supplier_iters = {s: iter(ps) for s, ps in sorted(by_supplier.items())}
        while len(sampled) < args.sample and supplier_iters:
            exhausted = []
            for s, it in supplier_iters.items():
                if len(sampled) >= args.sample:
                    break
                try:
                    sampled.append(next(it))
                except StopIteration:
                    exhausted.append(s)
            for s in exhausted:
                del supplier_iters[s]

        remaining = sampled
        print(f"Sampled {len(remaining)} PDFs from {len(by_supplier)} suppliers")

    # Process PDFs in parallel
    total = len(remaining)
    completed = 0
    total_stats = {"sections": 0, "errors": 0, "troubleshooting": 0}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_pdf, pdf_info): pdf_info for pdf_info in remaining}

        for future in as_completed(futures):
            pdf_info = futures[future]
            completed += 1
            try:
                stats = future.result()
                total_stats["sections"] += stats["sections"]
                total_stats["errors"] += stats["errors"]
                total_stats["troubleshooting"] += stats["troubleshooting"]

                with _checkpoint_lock:
                    _done.add(pdf_info["path"])
                    if completed % 5 == 0:
                        save_checkpoint(_done)

                print(f"  [{completed}/{total}] {stats['name'][:50]} "
                      f"=> {stats['sections']} sections, "
                      f"{stats['errors']} errors, "
                      f"{stats['troubleshooting']} troubleshooting")
            except Exception as e:
                print(f"  [{completed}/{total}] ERROR {pdf_info['name']}: {e}")

    with _checkpoint_lock:
        save_checkpoint(_done)

    # Cross-link parts to manual sections
    if not args.sample:
        cross_link_parts()

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  PDF seed completed in {elapsed:.1f}s")
    print(f"  ManualSections: {total_stats['sections']}")
    print(f"  ErrorCodes: {total_stats['errors']}")
    print(f"  Troubleshooting: {total_stats['troubleshooting']}")
    print(f"{'=' * 60}")

    # Verify
    print("\n  Verification:")
    for label in ["ManualSection", "ErrorCode", "TroubleshootingEntry", "Supplier"]:
        c = db.node_count(label)
        print(f"    {label}: {c}")
    for rel in ["HAS_ERROR", "HAS_TROUBLESHOOTING", "MENTIONED_IN", "FROM_SUPPLIER"]:
        c = db.rel_count(rel)
        print(f"    {rel}: {c}")


if __name__ == "__main__":
    main()
