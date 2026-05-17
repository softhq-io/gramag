"""Deprecated Gemini bulk ingest experiment.

Runtime code now uses Azure OpenAI via ai_client.py.

Gramag Mini PoC — Step 1: Ingest everything into a single JSON index.
PDFs + ERP key tables → chunks with embeddings.
"""
import os, json, csv, time, fitz
from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY)
EMBED_MODEL = "gemini-embedding-001"

DATA = "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten"
OUT = "/Users/piotrzwolinski/projects/gramag/index.json"


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (max 100)."""
    result = client.models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return [e.values for e in result.embeddings]


def load_csv(name):
    path = os.path.join(DATA, "ERP", name)
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ── 1. Extract PDFs ────────────────────────────────────────────────────

def ingest_pdfs():
    chunks = []
    pdf_root = os.path.join(DATA, "Servicedaten")

    for root, _, files in os.walk(pdf_root):
        for fname in files:
            if not fname.lower().endswith(".pdf"):
                continue
            path = os.path.join(root, fname)
            supplier = path.split("Lieferanten/")[-1].split("/")[0] if "Lieferanten" in path else "?"

            try:
                doc = fitz.open(path)
                # Merge pages into ~1500 char chunks
                buf, buf_pages = "", []
                for i, page in enumerate(doc):
                    text = page.get_text().strip()
                    if not text:
                        continue
                    if buf and len(buf) + len(text) > 1500:
                        chunks.append({
                            "text": buf[:2000],
                            "source": f"PDF: {fname}",
                            "supplier": supplier,
                            "pages": buf_pages[:],
                            "type": "pdf",
                        })
                        buf, buf_pages = text, [i + 1]
                    else:
                        buf += "\n" + text
                        buf_pages.append(i + 1)
                if buf.strip():
                    chunks.append({
                        "text": buf[:2000],
                        "source": f"PDF: {fname}",
                        "supplier": supplier,
                        "pages": buf_pages[:],
                        "type": "pdf",
                    })
                doc.close()
            except Exception as e:
                print(f"  SKIP {fname}: {e}")

    return chunks


# ── 2. Extract ERP data ────────────────────────────────────────────────

def ingest_erp():
    chunks = []

    # Machines (produkte)
    print("  Machines...")
    for p in load_csv("produkte.csv"):
        title = p.get("titel", "").strip()
        desc = p.get("beschreibung", "").strip()
        sn = p.get("seriennummer", "").strip()
        if not title:
            continue
        text = f"Maschine: {title}"
        if sn:
            text += f" | Seriennummer: {sn}"
        if desc:
            text += f" | {desc[:200]}"
        chunks.append({
            "text": text,
            "source": f"ERP Maschine ID:{p['id']}",
            "type": "machine",
            "erp_id": p["id"],
        })

    # Articles (spare parts)
    print("  Articles...")
    for a in load_csv("artikel.csv"):
        title = a.get("lang1Titel", "").strip()
        mfr = a.get("herstellerNr", "").strip()
        nr = a.get("nummer", "").strip()
        if not title:
            continue
        text = f"Ersatzteil [{nr}]: {title}"
        if mfr:
            text += f" | Hersteller-Nr: {mfr}"
        chunks.append({
            "text": text,
            "source": f"ERP Artikel [{nr}]",
            "type": "article",
            "erp_id": a["id"],
        })

    # Service comments (richest free text)
    print("  Comments...")
    import re
    for k in load_csv("kommentare.csv"):
        comment = k.get("kommentar", "").strip()
        if len(comment) < 30:
            continue
        clean = re.sub(r'<[^>]+>', '', comment).strip()
        if len(clean) < 30:
            continue
        ref_type = k.get("ref_typ", "")
        ref_id = k.get("ref_id", "")
        chunks.append({
            "text": clean[:1500],
            "source": f"ERP Kommentar ({ref_type}:{ref_id})",
            "type": "comment",
        })

    # Service job titles
    print("  Service jobs...")
    for d in load_csv("dokumente.csv"):
        if d.get("typ") != "s":
            continue
        title = d.get("bezeichnung", "").strip()
        desc = d.get("beschreibung", "").strip()
        if not title:
            continue
        text = f"Serviceauftrag {d.get('nummer','')}: {title}"
        if desc:
            clean = re.sub(r'<[^>]+>', '', desc).strip()
            text += f" | {clean[:300]}"
        chunks.append({
            "text": text[:1500],
            "source": f"ERP Serviceauftrag {d.get('nummer','')}",
            "type": "service_job",
        })

    return chunks


# ── 3. Embed everything ────────────────────────────────────────────────

def embed_all(chunks):
    print(f"\nEmbedding {len(chunks)} chunks...")
    BATCH = 100
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i+BATCH]
        texts = [c["text"] for c in batch]
        try:
            embeddings = embed_batch(texts)
            for c, emb in zip(batch, embeddings):
                c["embedding"] = emb
        except Exception as e:
            print(f"  Error at batch {i}: {e}")
            time.sleep(2)
            # Retry once
            try:
                embeddings = embed_batch(texts)
                for c, emb in zip(batch, embeddings):
                    c["embedding"] = emb
            except:
                print(f"  SKIP batch {i}")
                for c in batch:
                    c["embedding"] = []

        if (i // BATCH) % 10 == 0:
            print(f"  {i}/{len(chunks)} done...")
        time.sleep(0.1)  # Rate limit


# ── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Gramag Ingestion ===\n")

    print("[1/3] Ingesting PDFs...")
    pdf_chunks = ingest_pdfs()
    print(f"  → {len(pdf_chunks)} chunks from PDFs\n")

    print("[2/3] Ingesting ERP data...")
    erp_chunks = ingest_erp()
    print(f"  → {len(erp_chunks)} chunks from ERP\n")

    all_chunks = pdf_chunks + erp_chunks
    print(f"Total chunks: {len(all_chunks)}")

    print("\n[3/3] Generating embeddings...")
    embed_all(all_chunks)

    # Save (without embeddings in a readable summary, full data in index)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False)

    size_mb = os.path.getsize(OUT) / 1024 / 1024
    print(f"\nDone! Index saved to {OUT} ({size_mb:.1f} MB)")
    print(f"  PDF chunks:     {len(pdf_chunks)}")
    print(f"  ERP chunks:     {len(erp_chunks)}")
    print(f"  Total:          {len(all_chunks)}")
