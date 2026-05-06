"""
Compare text extraction (PyMuPDF) vs vision extraction (Gemini 3 Flash)
on pages with heavy visual content.

Tests whether LLM vision can extract knowledge that text extraction misses.
"""

import json
import time
from pathlib import Path

import fitz
from google import genai
from google.genai import types

from config import GEMINI_API_KEY

PDF_PATH = Path("/Users/piotrzwolinski/Downloads/DPMOpgUE.pdf")
CACHE_DIR = Path(__file__).parent / "test_embed_cache"
GEN_MODEL = "gemini-3-flash-preview"

# Pages with heavy visual content
VISUAL_PAGES = [5, 6, 7, 9, 15, 19]

client = genai.Client(api_key=GEMINI_API_KEY)

EXTRACTION_PROMPT = """\
You are a technical documentation analyst for industrial machines.

Analyze this manual page image. Extract ALL knowledge visible on this page.

IMPORTANT: Focus especially on information that is ONLY visible in the images/diagrams
and would NOT be captured by copy-pasting the text from this page. For example:
- Spatial positions of components ("RS232 port is third from top on the left side")
- Physical appearance (color, shape, size relative to other parts)
- How components connect to each other visually
- Button icons and their layout
- Warning symbol graphics
- Assembly sequences shown in photos

Structure your response as:

## Components (from diagrams/photos)
For each labeled component: [ref letter/number] Name — spatial position, visual description

## Visual-only knowledge
Facts that are ONLY visible in images, not written in text on this page.

## Procedures shown visually
Any step-by-step actions depicted in photos/diagrams.

## Relationships
How components connect, what is adjacent to what.

Be thorough but concise. A service technician will use this to find parts on the real machine."""


def extract_text(pdf_path: Path, page_num: int) -> str:
    doc = fitz.open(pdf_path)
    text = doc[page_num - 1].get_text().strip()
    doc.close()
    return text


def extract_vision(img_path: str) -> str:
    with open(img_path, "rb") as f:
        img_bytes = f.read()

    response = client.models.generate_content(
        model=GEN_MODEL,
        contents=[
            types.Content(role="user", parts=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                types.Part(text=EXTRACTION_PROMPT),
            ])
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=3000,
        ),
    )
    return response.text


def main():
    print("=" * 70)
    print("VISION EXTRACTION vs TEXT EXTRACTION")
    print("=" * 70)

    all_results = []

    for page_num in VISUAL_PAGES:
        img_path = CACHE_DIR / "page_images" / f"page_{page_num:03d}.png"
        if not img_path.exists():
            print(f"  [skip] page {page_num} image not found")
            continue

        print(f"\n{'─' * 70}")
        print(f"PAGE {page_num}")
        print(f"{'─' * 70}")

        # Text extraction
        text = extract_text(PDF_PATH, page_num)
        text_words = len(text.split())
        print(f"\n  [TEXT] PyMuPDF: {text_words} words, {len(text)} chars")
        preview = text[:300].replace("\n", " ↵ ")
        print(f"  {preview}...")

        # Vision extraction
        print(f"\n  [VISION] Gemini 3 Flash analyzing image...")
        t0 = time.time()
        vision_text = extract_vision(str(img_path))
        t_vision = time.time() - t0
        vision_words = len(vision_text.split())
        print(f"  Extracted in {t_vision:.1f}s: {vision_words} words")
        # Print full vision output
        for line in vision_text.strip().split("\n"):
            print(f"  {line}")

        all_results.append({
            "page": page_num,
            "text_words": text_words,
            "vision_words": vision_words,
            "vision_text": vision_text,
            "vision_time_s": round(t_vision, 1),
        })

        time.sleep(1)

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n  {'Page':<6} {'Text (words)':<14} {'Vision (words)':<16} {'Ratio':<8} {'Time'}")
    print(f"  {'─'*6} {'─'*14} {'─'*16} {'─'*8} {'─'*6}")
    for r in all_results:
        ratio = r["vision_words"] / max(r["text_words"], 1)
        print(f"  p.{r['page']:<3} {r['text_words']:<14} {r['vision_words']:<16} {ratio:<8.1f} {r['vision_time_s']}s")

    # Save full results
    out = CACHE_DIR / "vision_extraction.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n  Full results saved to {out}")


if __name__ == "__main__":
    main()
