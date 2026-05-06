"""Quick Baumer sample — 3 biggest unique PDFs, full extraction."""
import os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from seed_pdfs import process_pdf, extract_text_by_page, smart_chunk
from db import db
from db_helpers import result_value

db.connect()

baumer_dir = "data/Gramag Daten/Servicedaten/Lieferanten/Baumer hhs"
pdfs = []
for root, _, files in os.walk(baumer_dir):
    for f in files:
        if f.lower().endswith(".pdf"):
            pdfs.append({"path": os.path.join(root, f), "name": f, "supplier": "Baumer hhs"})

seen, unique = set(), []
for p in sorted(pdfs, key=lambda p: os.path.getsize(p["path"]), reverse=True):
    if p["name"] not in seen:
        seen.add(p["name"])
        unique.append(p)
unique = unique[:3]

print(f"Processing {len(unique)} Baumer PDFs (full LLM extraction):")
for p in unique:
    size = os.path.getsize(p["path"]) / 1024 / 1024
    pages = extract_text_by_page(p["path"])
    chunks = smart_chunk(pages)
    print(f"  {p['name'][:55]:55} {size:6.1f}MB  {len(pages):3}p  {len(chunks):3}ch")

t0 = time.time()
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {pool.submit(process_pdf, p): p for p in unique}
    for i, future in enumerate(as_completed(futures), 1):
        stats = future.result()
        print(f"  [{i}/{len(unique)}] {stats['name'][:50]} => "
              f"{stats['sections']}sec {stats['errors']}err {stats['troubleshooting']}ts")

print(f"\nDone in {time.time()-t0:.1f}s")
for label in ["ManualSection", "ErrorCode", "TroubleshootingEntry"]:
    c = result_value(db.query(
        f"MATCH (n:{label}) WHERE n.supplier = 'Baumer hhs' RETURN count(n) AS c"
    ), "c", 0)
    print(f"  {label}: {c}")
