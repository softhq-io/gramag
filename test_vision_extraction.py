"""
Compare text extraction (PyMuPDF) vs Azure OpenAI vision extraction
on pages with heavy visual content.

Tests whether LLM vision can extract knowledge that text extraction misses.
"""

from pathlib import Path

import fitz
from proto.vision import vision_extract_page

PDF_PATH = Path("/Users/piotrzwolinski/Downloads/DPMOpgUE.pdf")
CACHE_DIR = Path(__file__).parent / "test_embed_cache"

# Pages with heavy visual content
VISUAL_PAGES = [5, 6, 7, 9, 15, 19]

def extract_text(pdf_path: Path, page_num: int) -> str:
    doc = fitz.open(pdf_path)
    text = doc[page_num - 1].get_text().strip()
    doc.close()
    return text


def extract_vision(img_path: str) -> str:
    return vision_extract_page(img_path)


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
