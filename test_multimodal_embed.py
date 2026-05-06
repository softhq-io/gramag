"""
Side-by-side comparison: text embeddings vs multimodal image embeddings
on the DPM/PEM operating instructions PDF.

Usage:
    python test_multimodal_embed.py                  # index + query
    python test_multimodal_embed.py --query-only      # skip indexing, reuse cache
"""

import json
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from google import genai
from google.genai import types

from config import GEMINI_API_KEY

# ── Config ──────────────────────────────────────────────────────────────
PDF_PATH = Path("/Users/piotrzwolinski/Downloads/DPMOpgUE.pdf")
CACHE_DIR = Path(__file__).parent / "test_embed_cache"
TEXT_MODEL = "gemini-embedding-001"
IMAGE_MODEL = "gemini-embedding-2-preview"
DIM = 768  # smaller dim for speed; both models support 768
DPI = 200  # render resolution

GEN_MODEL = "gemini-3-flash-preview"

TEST_QUERIES = [
    "Where is the RS232 port on the DPM?",
    "How to insert a CF memory card?",
    "What are the DPM operating parts?",
    "How to carry the printer safely?",
    "What does the control panel display show?",
    "How to switch from offline to online mode?",
    "What warning labels are on the printer?",
    "How to connect the power cable?",
    "What is the pinch point warning about?",
    "Where is the ribbon rewinding mandrel?",
]

client = genai.Client(api_key=GEMINI_API_KEY)


# ── PDF → pages ─────────────────────────────────────────────────────────
def extract_pages(pdf_path: Path) -> list[dict]:
    """Extract text and render images for each page."""
    doc = fitz.open(pdf_path)
    pages = []
    img_dir = CACHE_DIR / "page_images"
    img_dir.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(doc):
        text = page.get_text().strip()
        # Render page as PNG
        pix = page.get_pixmap(dpi=DPI)
        img_path = img_dir / f"page_{i+1:03d}.png"
        pix.save(str(img_path))

        pages.append({
            "page_num": i + 1,
            "text": text,
            "img_path": str(img_path),
            "text_len": len(text),
        })
    doc.close()
    return pages


# ── Embedding helpers ───────────────────────────────────────────────────
def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    result = client.models.embed_content(
        model=TEXT_MODEL,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=DIM,
        ),
    )
    return list(result.embeddings[0].values)


def embed_image(img_path: str) -> list[float]:
    with open(img_path, "rb") as f:
        img_bytes = f.read()
    result = client.models.embed_content(
        model=IMAGE_MODEL,
        contents=[types.Part.from_bytes(data=img_bytes, mime_type="image/png")],
        config=types.EmbedContentConfig(output_dimensionality=DIM),
    )
    return list(result.embeddings[0].values)


def embed_query_text(query: str) -> list[float]:
    return embed_text(query, task_type="RETRIEVAL_QUERY")


def embed_query_image_model(query: str) -> list[float]:
    """Embed query using the IMAGE model (same vector space as image embeddings)."""
    result = client.models.embed_content(
        model=IMAGE_MODEL,
        contents=query,
        config=types.EmbedContentConfig(output_dimensionality=DIM),
    )
    return list(result.embeddings[0].values)


# ── Indexing ────────────────────────────────────────────────────────────
def build_indices(pages: list[dict]) -> dict:
    """Build text and image embedding indices."""
    text_vecs = []
    image_vecs = []
    n = len(pages)

    print(f"\n{'='*60}")
    print(f"INDEXING {n} pages")
    print(f"{'='*60}")

    # Text embeddings
    print(f"\n[1/2] Text embeddings ({TEXT_MODEL}, {DIM}d)...")
    for i, p in enumerate(pages):
        txt = p["text"][:2000] if p["text"] else "(empty page)"
        try:
            vec = embed_text(txt)
            text_vecs.append(vec)
        except Exception as e:
            print(f"  Page {p['page_num']}: text embed error: {e}")
            text_vecs.append([0.0] * DIM)
        if i % 5 == 4:
            print(f"  {i+1}/{n} done")
            time.sleep(0.5)
        else:
            time.sleep(0.15)
    print(f"  ✓ {n} text embeddings")

    # Image embeddings
    print(f"\n[2/2] Image embeddings ({IMAGE_MODEL}, {DIM}d)...")
    for i, p in enumerate(pages):
        try:
            vec = embed_image(p["img_path"])
            image_vecs.append(vec)
        except Exception as e:
            print(f"  Page {p['page_num']}: image embed error: {e}")
            image_vecs.append([0.0] * DIM)
        if i % 5 == 4:
            print(f"  {i+1}/{n} done")
            time.sleep(0.5)
        else:
            time.sleep(0.15)
    print(f"  ✓ {n} image embeddings")

    return {
        "text": np.array(text_vecs, dtype=np.float32),
        "image": np.array(image_vecs, dtype=np.float32),
    }


def save_indices(indices: dict, pages: list[dict]):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(CACHE_DIR / "text_index.npy", indices["text"])
    np.save(CACHE_DIR / "image_index.npy", indices["image"])
    with open(CACHE_DIR / "pages.json", "w") as f:
        json.dump(pages, f, indent=2)


def load_indices() -> tuple[dict, list[dict]]:
    indices = {
        "text": np.load(CACHE_DIR / "text_index.npy"),
        "image": np.load(CACHE_DIR / "image_index.npy"),
    }
    with open(CACHE_DIR / "pages.json") as f:
        pages = json.load(f)
    return indices, pages


# ── Search ──────────────────────────────────────────────────────────────
def cosine_search(query_vec: np.ndarray, index: np.ndarray, top_k: int = 5) -> list[tuple[int, float]]:
    """Return (index, score) pairs sorted by cosine similarity."""
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(index, axis=1, keepdims=True) + 1e-10
    index_norm = index / norms
    scores = index_norm @ query_norm
    top_idx = np.argsort(scores)[::-1][:top_k]
    return [(int(i), float(scores[i])) for i in top_idx]


def run_queries(indices: dict, pages: list[dict]):
    """Run all test queries and compare text vs image retrieval."""
    print(f"\n{'='*60}")
    print("RETRIEVAL COMPARISON")
    print(f"{'='*60}")

    summary = {"text_wins": 0, "image_wins": 0, "ties": 0}

    for q_idx, query in enumerate(TEST_QUERIES):
        # Embed query for both models
        q_text = np.array(embed_query_text(query), dtype=np.float32)
        time.sleep(0.2)
        q_image = np.array(embed_query_image_model(query), dtype=np.float32)
        time.sleep(0.2)

        text_results = cosine_search(q_text, indices["text"], top_k=3)
        image_results = cosine_search(q_image, indices["image"], top_k=3)

        print(f"\n── Q{q_idx+1}: \"{query}\"")
        print(f"  {'TEXT embeddings':<35} {'IMAGE embeddings':<35}")
        print(f"  {'─'*33}   {'─'*33}")

        for rank in range(3):
            t_idx, t_score = text_results[rank]
            i_idx, i_score = image_results[rank]
            t_page = pages[t_idx]
            i_page = pages[i_idx]

            t_preview = t_page["text"][:50].replace("\n", " ") if t_page["text"] else "(empty)"
            i_preview = i_page["text"][:50].replace("\n", " ") if i_page["text"] else "(empty)"

            marker_t = "◀" if rank == 0 else " "
            marker_i = "◀" if rank == 0 else " "

            print(f"  {rank+1}. p.{t_page['page_num']:>2} ({t_score:.3f}) {t_preview[:28]:<28} "
                  f"  {rank+1}. p.{i_page['page_num']:>2} ({i_score:.3f}) {i_preview[:28]:<28}")

        # Track if top-1 differs
        if text_results[0][0] == image_results[0][0]:
            summary["ties"] += 1
        else:
            # We can't auto-judge quality, just note the difference
            print(f"  → Different top-1: text=p.{pages[text_results[0][0]]['page_num']} "
                  f"vs image=p.{pages[image_results[0][0]]['page_num']}")

    n = len(TEST_QUERIES)
    diff = n - summary["ties"]
    print(f"\n{'='*60}")
    print(f"SUMMARY: {summary['ties']}/{n} queries agree on top-1, {diff}/{n} differ")
    print(f"{'='*60}")


# ── Generation (Gemini 3 Flash with page images) ───────────────────────
SYSTEM_PROMPT = """You are a technical service assistant for industrial printing machines.
You answer questions from service technicians based on the provided manual pages.

Rules:
- Answer based ONLY on the provided pages. If the answer isn't visible, say so.
- Reference specific page numbers and figure numbers when possible.
- If the answer involves a diagram or photo, describe what is shown and where to look.
- Be concise and practical — technicians need quick answers.
- Answer in the same language as the question."""


def generate_answer(query: str, page_indices: list[int], pages: list[dict], mode: str) -> str:
    """Generate answer using Gemini 3 Flash with retrieved page images."""
    parts = []

    # Add page images
    for idx in page_indices:
        p = pages[idx]
        with open(p["img_path"], "rb") as f:
            img_bytes = f.read()
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        parts.append(types.Part(text=f"[Above: Manual page {p['page_num']}]"))

    # Add the question
    parts.append(types.Part(text=f"\nQuestion: {query}"))

    response = client.models.generate_content(
        model=GEN_MODEL,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=500,
        ),
    )
    return response.text


def run_generation_comparison(indices: dict, pages: list[dict]):
    """Compare generation quality: text-retrieved pages vs image-retrieved pages."""
    # Pick a subset of queries that showed interesting differences
    gen_queries = [
        "Where is the RS232 port on the DPM?",
        "How to insert a CF memory card?",
        "What is the pinch point warning about?",
        "Where is the ribbon rewinding mandrel?",
        "How to carry the printer safely?",
    ]

    print(f"\n{'='*70}")
    print("GENERATION COMPARISON — Gemini 3 Flash with page images")
    print(f"{'='*70}")

    for q_idx, query in enumerate(gen_queries):
        # Get top-3 from each index
        q_text = np.array(embed_query_text(query), dtype=np.float32)
        time.sleep(0.2)
        q_image = np.array(embed_query_image_model(query), dtype=np.float32)
        time.sleep(0.2)

        text_results = cosine_search(q_text, indices["text"], top_k=3)
        image_results = cosine_search(q_image, indices["image"], top_k=3)

        # Merge & deduplicate for hybrid
        seen = set()
        hybrid_indices = []
        for idx, _ in text_results + image_results:
            if idx not in seen:
                seen.add(idx)
                hybrid_indices.append(idx)
        hybrid_indices = hybrid_indices[:4]  # max 4 pages

        text_page_indices = [idx for idx, _ in text_results[:3]]
        image_page_indices = [idx for idx, _ in image_results[:3]]

        text_pages_str = ", ".join(str(pages[i]["page_num"]) for i in text_page_indices)
        image_pages_str = ", ".join(str(pages[i]["page_num"]) for i in image_page_indices)
        hybrid_pages_str = ", ".join(str(pages[i]["page_num"]) for i in hybrid_indices)

        print(f"\n{'─'*70}")
        print(f"Q{q_idx+1}: \"{query}\"")
        print(f"{'─'*70}")

        # A) Text-retrieved → Gemini with images
        print(f"\n  [A] TEXT retrieval → pages {text_pages_str}")
        try:
            ans_text = generate_answer(query, text_page_indices, pages, "text")
            print(f"  {ans_text}")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(1)

        # B) Image-retrieved → Gemini with images
        print(f"\n  [B] IMAGE retrieval → pages {image_pages_str}")
        try:
            ans_image = generate_answer(query, image_page_indices, pages, "image")
            print(f"  {ans_image}")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(1)

        # C) Hybrid-retrieved → Gemini with images
        print(f"\n  [C] HYBRID retrieval → pages {hybrid_pages_str}")
        try:
            ans_hybrid = generate_answer(query, hybrid_indices, pages, "hybrid")
            print(f"  {ans_hybrid}")
        except Exception as e:
            print(f"  ERROR: {e}")
        time.sleep(1)


# ── Main ────────────────────────────────────────────────────────────────
def main():
    query_only = "--query-only" in sys.argv

    if query_only and (CACHE_DIR / "text_index.npy").exists():
        print("Loading cached indices...")
        indices, pages = load_indices()
    else:
        print(f"Processing: {PDF_PATH.name}")
        pages = extract_pages(PDF_PATH)
        print(f"Extracted {len(pages)} pages")
        for p in pages:
            print(f"  p.{p['page_num']:>2}: {p['text_len']:>4} chars | {p['img_path']}")

        indices = build_indices(pages)
        save_indices(indices, pages)
        print("\nIndices cached to", CACHE_DIR)

    run_queries(indices, pages)
    run_generation_comparison(indices, pages)


if __name__ == "__main__":
    main()
