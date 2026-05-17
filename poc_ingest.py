"""Deprecated Gemini proof-of-concept script.

Runtime code now uses Azure OpenAI via ai_client.py. This file is retained
only as historical exploration and is not part of the supported local run path.

Gramag PDF Ingestion — Proof of Concept
Tests 3 tiers on 5 diverse PDFs to validate quality before scaling.

Tier 1: Text extraction + smart chunking + embeddings
Tier 2: LLM structured extraction (error codes, procedures, parts)
Tier 3: Cross-linking with ERP data
"""
import os
import json
import fitz  # PyMuPDF
import re
import time
from google import genai
from google.genai import types
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY)

FLASH_MODEL = "gemini-2.0-flash"
EMBED_MODEL = "gemini-embedding-001"

POC_PDFS = [
    {
        "path": "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/Servicedaten/Lieferanten/Avery/Printer/Docu/Docu200/64xerrse.pdf",
        "supplier": "Avery",
        "expected_content": "Error codes / fault location for 64-xx printers",
    },
    {
        "path": "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/Servicedaten/Lieferanten/BDT/TPF Feeder Vorführmaschine/User Manual_BDT Feeder_Rev_10.pdf",
        "supplier": "BDT",
        "expected_content": "User guide with operation and maintenance procedures",
    },
    {
        "path": "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/Servicedaten/Lieferanten/Baumer hhs/Steuergeräte/XTS2/XTS-2-FC-DE-20160704.pdf",
        "supplier": "Baumer hhs",
        "expected_content": "Large operations manual for XTS² control unit",
    },
    {
        "path": "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/Servicedaten/Lieferanten/Baumer hhs/Steuergeräte/C-221/Ersatzteilliste_C221_72262210.pdf",
        "supplier": "Baumer hhs",
        "expected_content": "Spare parts list for C-221 controller",
    },
    {
        "path": "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/Servicedaten/Lieferanten/Avery/Labeller_and_PandA/Docu/MA/ALX92x_Ma_AnCe_DE.pdf",
        "supplier": "Avery",
        "expected_content": "Maintenance manual for ALX 92x labeller",
    },
]

OUTPUT_DIR = "/Users/piotrzwolinski/projects/gramag/poc_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ═══════════════════════════════════════════════════════════════════════
# TIER 1: Text Extraction + Smart Chunking
# ═══════════════════════════════════════════════════════════════════════

def extract_text_by_page(pdf_path: str) -> list[dict]:
    """Extract text from each page, preserving page numbers."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            pages.append({
                "page_num": i + 1,
                "text": text.strip(),
                "char_count": len(text.strip()),
            })
    doc.close()
    return pages


def smart_chunk(pages: list[dict], max_chunk_chars: int = 1500, overlap_chars: int = 200) -> list[dict]:
    """
    Chunk by page boundaries with smart merging.
    Short pages get merged with neighbors. Long pages get split at paragraph breaks.
    """
    chunks = []
    buffer = ""
    buffer_pages = []

    for page in pages:
        text = page["text"]
        page_num = page["page_num"]

        # If adding this page exceeds limit, flush buffer
        if buffer and len(buffer) + len(text) > max_chunk_chars:
            chunks.append({
                "text": buffer.strip(),
                "pages": list(buffer_pages),
                "char_count": len(buffer.strip()),
            })
            # Keep overlap from end of buffer
            if overlap_chars > 0:
                buffer = buffer[-overlap_chars:]
                buffer_pages = [buffer_pages[-1]] if buffer_pages else []
            else:
                buffer = ""
                buffer_pages = []

        # If single page exceeds limit, split at paragraph breaks
        if len(text) > max_chunk_chars:
            # Flush buffer first
            if buffer.strip():
                chunks.append({
                    "text": buffer.strip(),
                    "pages": list(buffer_pages),
                    "char_count": len(buffer.strip()),
                })
                buffer = ""
                buffer_pages = []

            # Split long page at double-newlines or single newlines
            paragraphs = re.split(r'\n\s*\n', text)
            para_buffer = ""
            for para in paragraphs:
                if len(para_buffer) + len(para) > max_chunk_chars:
                    if para_buffer.strip():
                        chunks.append({
                            "text": para_buffer.strip(),
                            "pages": [page_num],
                            "char_count": len(para_buffer.strip()),
                        })
                    para_buffer = para
                else:
                    para_buffer += "\n\n" + para
            if para_buffer.strip():
                buffer = para_buffer
                buffer_pages = [page_num]
        else:
            buffer += "\n\n" + text
            buffer_pages.append(page_num)

    # Flush remaining
    if buffer.strip():
        chunks.append({
            "text": buffer.strip(),
            "pages": list(buffer_pages),
            "char_count": len(buffer.strip()),
        })

    return chunks


def generate_embeddings(chunks: list[dict], doc_name: str) -> list[dict]:
    """Generate embeddings for chunks using Gemini."""
    texts = [c["text"][:2000] for c in chunks]  # Trim to embedding limit

    # Batch embed (max 100 per request)
    all_embeddings = []
    for i in range(0, len(texts), 100):
        batch = texts[i:i+100]
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
            ),
        )
        all_embeddings.extend([e.values for e in result.embeddings])

    for chunk, emb in zip(chunks, all_embeddings):
        chunk["embedding"] = emb
        chunk["embedding_dim"] = len(emb)

    return chunks


# ═══════════════════════════════════════════════════════════════════════
# TIER 2: LLM Structured Extraction
# ═══════════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """You are a technical documentation analyst for an industrial machine service company (Gramag — services printing, folding, cutting, enveloping, labelling machines).

Analyze this section of a technical manual and extract structured information. Return valid JSON only.

Document: {doc_name} (Supplier: {supplier})
Pages: {pages}

Extract the following (include only what's actually present, skip empty arrays):

{{
  "error_codes": [
    {{"code": "E01", "description": "...", "cause": "...", "solution": "..."}}
  ],
  "maintenance_procedures": [
    {{"title": "...", "frequency": "daily/weekly/monthly/yearly/as_needed", "steps": ["step1", "step2"], "tools_needed": ["..."], "parts_needed": ["..."]}}
  ],
  "spare_parts_mentioned": [
    {{"part_number": "...", "name": "...", "description": "...", "where_used": "..."}}
  ],
  "troubleshooting": [
    {{"symptom": "...", "possible_causes": ["..."], "solutions": ["..."]}}
  ],
  "safety_warnings": [
    {{"warning": "...", "severity": "danger/warning/caution"}}
  ],
  "technical_specs": [
    {{"parameter": "...", "value": "...", "unit": "..."}}
  ],
  "machine_models_covered": ["model1", "model2"],
  "section_summary": "One paragraph summary of what this section covers"
}}

RULES:
- Extract REAL data only — do not invent codes or procedures
- Keep original language for part numbers and technical terms
- Translate German descriptions to English in parentheses where helpful
- If a section is mostly diagrams/images with little text, note that in section_summary

TEXT TO ANALYZE:
{text}
"""


def extract_structured(chunk_text: str, doc_name: str, supplier: str, pages: list[int]) -> dict:
    """Use LLM to extract structured entities from a chunk."""
    prompt = EXTRACTION_PROMPT.format(
        doc_name=doc_name,
        supplier=supplier,
        pages=f"{pages[0]}-{pages[-1]}" if len(pages) > 1 else str(pages[0]),
        text=chunk_text[:6000],  # Stay within context
    )

    response = client.models.generate_content(
        model=FLASH_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        text = response.text
        start = text.find('{')
        end = text.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {"error": "Failed to parse JSON", "raw": text[:500]}


# ═══════════════════════════════════════════════════════════════════════
# TIER 3: Cross-linking with ERP
# ═══════════════════════════════════════════════════════════════════════

def load_erp_parts():
    """Load article numbers from ERP for cross-referencing."""
    import csv
    erp_path = "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/ERP/artikel.csv"
    parts = {}
    with open(erp_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            mfr = row.get("herstellerNr", "").strip()
            if mfr:
                parts[mfr] = {
                    "erp_id": row["id"],
                    "nummer": row.get("nummer", ""),
                    "titel": row.get("lang1Titel", ""),
                }
    return parts


def load_erp_machines():
    """Load machine models from ERP for cross-referencing."""
    import csv
    erp_path = "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/ERP/produkte.csv"
    machines = {}
    with open(erp_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            title = row.get("titel", "").strip()
            sn = row.get("seriennummer", "").strip()
            if title:
                machines[row["id"]] = {
                    "titel": title,
                    "seriennummer": sn,
                    "ref_kunde": row.get("ref_kunde", ""),
                }
    return machines


def cross_link(extracted: dict, erp_parts: dict, erp_machines: dict, doc_name: str) -> dict:
    """Find matches between PDF-extracted entities and ERP data."""
    links = {
        "part_matches": [],
        "machine_matches": [],
    }

    # Match part numbers from PDF against ERP manufacturer numbers
    pdf_parts = extracted.get("spare_parts_mentioned", [])
    for pp in pdf_parts:
        pn = pp.get("part_number", "")
        if pn and pn in erp_parts:
            links["part_matches"].append({
                "pdf_part": pn,
                "erp_artikel_id": erp_parts[pn]["erp_id"],
                "erp_nummer": erp_parts[pn]["nummer"],
                "erp_titel": erp_parts[pn]["titel"],
            })

    # Match machine models from PDF against ERP machine titles
    pdf_machines = extracted.get("machine_models_covered", [])
    for pm in pdf_machines:
        pm_lower = pm.lower()
        for mid, mdata in erp_machines.items():
            if pm_lower in mdata["titel"].lower() or pm in mdata["titel"]:
                links["machine_matches"].append({
                    "pdf_machine": pm,
                    "erp_produkt_id": mid,
                    "erp_titel": mdata["titel"],
                    "erp_sn": mdata["seriennummer"],
                })
                break  # First match per PDF machine

    return links


# ═══════════════════════════════════════════════════════════════════════
# RUN PROOF OF CONCEPT
# ═══════════════════════════════════════════════════════════════════════

def run_poc():
    section("GRAMAG PDF INGESTION — PROOF OF CONCEPT")

    # Load ERP data for cross-linking
    print("Loading ERP data for cross-linking...")
    erp_parts = load_erp_parts()
    erp_machines = load_erp_machines()
    print(f"  ERP parts with manufacturer numbers: {len(erp_parts)}")
    print(f"  ERP machines: {len(erp_machines)}")

    results = []

    for i, pdf_info in enumerate(POC_PDFS):
        path = pdf_info["path"]
        name = os.path.basename(path)
        supplier = pdf_info["supplier"]

        if not os.path.exists(path):
            print(f"\n  SKIP: {name} — file not found")
            continue

        section(f"PDF {i+1}/5: {name} ({supplier})")
        print(f"  Expected: {pdf_info['expected_content']}")

        # ── Tier 1: Extract + Chunk + Embed ─────────────────────────
        print("\n  [Tier 1] Text extraction + chunking...")
        pages = extract_text_by_page(path)
        total_chars = sum(p["char_count"] for p in pages)
        print(f"    Pages with text: {len(pages)}")
        print(f"    Total characters: {total_chars:,}")

        chunks = smart_chunk(pages)
        print(f"    Chunks created: {len(chunks)}")
        print(f"    Avg chunk size: {sum(c['char_count'] for c in chunks) // max(len(chunks),1)} chars")

        # Show first chunk preview
        if chunks:
            preview = chunks[0]["text"][:200].replace("\n", " ")
            print(f"    First chunk preview: {preview}...")

        # Embed (do first 5 chunks only for PoC speed)
        print("\n  [Tier 1] Generating embeddings...")
        embed_chunks = chunks[:5]
        t0 = time.time()
        embed_chunks = generate_embeddings(embed_chunks, name)
        embed_time = time.time() - t0
        print(f"    Embedded {len(embed_chunks)} chunks in {embed_time:.1f}s")
        print(f"    Embedding dimension: {embed_chunks[0]['embedding_dim']}")

        # ── Tier 2: LLM Structured Extraction ───────────────────────
        print("\n  [Tier 2] LLM structured extraction...")
        # Process first 3 chunks for PoC
        all_extracted = {
            "error_codes": [],
            "maintenance_procedures": [],
            "spare_parts_mentioned": [],
            "troubleshooting": [],
            "safety_warnings": [],
            "technical_specs": [],
            "machine_models_covered": [],
            "section_summaries": [],
        }

        extract_chunks = chunks[:3]  # Limit for PoC
        t0 = time.time()
        for j, chunk in enumerate(extract_chunks):
            print(f"    Extracting chunk {j+1}/{len(extract_chunks)} (pages {chunk['pages']})...")
            try:
                extracted = extract_structured(chunk["text"], name, supplier, chunk["pages"])

                # Merge results
                for key in ["error_codes", "maintenance_procedures", "spare_parts_mentioned",
                           "troubleshooting", "safety_warnings", "technical_specs"]:
                    all_extracted[key].extend(extracted.get(key, []))

                models = extracted.get("machine_models_covered", [])
                all_extracted["machine_models_covered"].extend(models)

                summary = extracted.get("section_summary", "")
                if summary:
                    all_extracted["section_summaries"].append(summary)

                time.sleep(0.5)  # Rate limit
            except Exception as e:
                print(f"    ERROR: {e}")

        extract_time = time.time() - t0

        # Deduplicate
        all_extracted["machine_models_covered"] = list(set(all_extracted["machine_models_covered"]))

        print(f"    Extraction time: {extract_time:.1f}s")
        print(f"    Results:")
        print(f"      Error codes:         {len(all_extracted['error_codes'])}")
        print(f"      Maint. procedures:   {len(all_extracted['maintenance_procedures'])}")
        print(f"      Spare parts:         {len(all_extracted['spare_parts_mentioned'])}")
        print(f"      Troubleshooting:     {len(all_extracted['troubleshooting'])}")
        print(f"      Safety warnings:     {len(all_extracted['safety_warnings'])}")
        print(f"      Technical specs:     {len(all_extracted['technical_specs'])}")
        print(f"      Machine models:      {all_extracted['machine_models_covered']}")

        # Show some extracted data
        if all_extracted["error_codes"]:
            print(f"\n    Sample error codes:")
            for ec in all_extracted["error_codes"][:3]:
                print(f"      {ec.get('code','?')}: {ec.get('description','?')[:60]}")

        if all_extracted["troubleshooting"]:
            print(f"\n    Sample troubleshooting:")
            for ts in all_extracted["troubleshooting"][:2]:
                print(f"      Symptom: {ts.get('symptom','?')[:60]}")
                print(f"      Causes:  {ts.get('possible_causes',['?'])[:2]}")

        if all_extracted["maintenance_procedures"]:
            print(f"\n    Sample procedures:")
            for mp in all_extracted["maintenance_procedures"][:2]:
                print(f"      {mp.get('title','?')[:60]} ({mp.get('frequency','?')})")

        if all_extracted["spare_parts_mentioned"]:
            print(f"\n    Sample parts mentioned:")
            for sp in all_extracted["spare_parts_mentioned"][:3]:
                print(f"      {sp.get('part_number','?')}: {sp.get('name','?')[:50]}")

        # ── Tier 3: Cross-link with ERP ──────────────────────────────
        print(f"\n  [Tier 3] Cross-linking with ERP...")
        links = cross_link(all_extracted, erp_parts, erp_machines, name)
        print(f"    Part matches (PDF → ERP):    {len(links['part_matches'])}")
        print(f"    Machine matches (PDF → ERP): {len(links['machine_matches'])}")

        if links["part_matches"]:
            print(f"\n    Part cross-references found:")
            for pm in links["part_matches"][:5]:
                print(f"      PDF [{pm['pdf_part']}] → ERP [{pm['erp_nummer']}] {pm['erp_titel'][:50]}")

        if links["machine_matches"]:
            print(f"\n    Machine cross-references found:")
            for mm in links["machine_matches"][:5]:
                print(f"      PDF [{mm['pdf_machine']}] → ERP [{mm['erp_titel'][:50]}]")

        # Save full results
        result = {
            "pdf": name,
            "supplier": supplier,
            "tier1": {
                "pages": len(pages),
                "total_chars": total_chars,
                "chunks": len(chunks),
                "sample_chunk": chunks[0]["text"][:500] if chunks else "",
            },
            "tier2": all_extracted,
            "tier3": links,
        }
        results.append(result)

        # Save individual result
        out_path = os.path.join(OUTPUT_DIR, f"{name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n  Saved to: {out_path}")

    # ── Summary ──────────────────────────────────────────────────────
    section("PROOF OF CONCEPT SUMMARY")

    total_errors = sum(len(r["tier2"]["error_codes"]) for r in results)
    total_procedures = sum(len(r["tier2"]["maintenance_procedures"]) for r in results)
    total_parts = sum(len(r["tier2"]["spare_parts_mentioned"]) for r in results)
    total_troubleshoot = sum(len(r["tier2"]["troubleshooting"]) for r in results)
    total_part_matches = sum(len(r["tier3"]["part_matches"]) for r in results)
    total_machine_matches = sum(len(r["tier3"]["machine_matches"]) for r in results)

    print(f"""
PDFs processed:           {len(results)}

Tier 1 (Text + Embed):
  Total characters:       {sum(r['tier1']['total_chars'] for r in results):,}
  Total chunks:           {sum(r['tier1']['chunks'] for r in results)}

Tier 2 (LLM Extraction):
  Error codes:            {total_errors}
  Maintenance procedures: {total_procedures}
  Spare parts mentioned:  {total_parts}
  Troubleshooting steps:  {total_troubleshoot}

Tier 3 (ERP Cross-links):
  Part matches:           {total_part_matches}
  Machine matches:        {total_machine_matches}
""")


if __name__ == "__main__":
    run_poc()
