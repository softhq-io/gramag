"""Benchmark sequential vs parallel vision extraction on one PDF."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz

from proto.vision import vision_extract_page, with_retry

PDF = Path("/Users/piotrzwolinski/Downloads/drive-download-20260414T171809Z-3-001/SMB/S02 Nr 600A021408001/pdf/d00709a.pdf")
OUT = Path("/Users/piotrzwolinski/projects/gramag/proto/cache/bench_pages")
OUT.mkdir(parents=True, exist_ok=True)


def render_pages(pdf_path: Path, n: int | None = None) -> list[Path]:
    doc = fitz.open(pdf_path)
    pages = list(doc)[:n] if n else list(doc)
    paths = []
    for i, page in enumerate(pages, start=1):
        png = OUT / f"p{i:04d}.png"
        if not png.exists():
            page.get_pixmap(dpi=150).save(png)
        paths.append(png)
    doc.close()
    return paths


def bench_serial(paths: list[Path]) -> float:
    t0 = time.time()
    for p in paths:
        with_retry(vision_extract_page, str(p))
    return time.time() - t0


def bench_parallel(paths: list[Path], workers: int) -> float:
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(with_retry, vision_extract_page, str(p)) for p in paths]
        for f in as_completed(futures):
            f.result()
    return time.time() - t0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    paths = render_pages(PDF, n=n)
    print(f"Benchmark: {len(paths)} pages from {PDF.name}")

    print("\n[1/2] SERIAL (workers=1)...")
    t_serial = bench_serial(paths)
    print(f"  done in {t_serial:.1f}s  ({t_serial/len(paths):.1f}s/page)")

    for w in (4, 8):
        print(f"\n[2/2] PARALLEL (workers={w})...")
        t_par = bench_parallel(paths, w)
        speedup = t_serial / t_par
        print(f"  done in {t_par:.1f}s  ({t_par/len(paths):.1f}s/page) — {speedup:.1f}x speedup")


if __name__ == "__main__":
    main()
