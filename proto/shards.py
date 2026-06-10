"""Generate balanced Proto ingest shard definitions from a manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

from proto import PROTO_MANIFEST_PATH, resolve_source


def _pdf_pages(path: str) -> int:
    try:
        doc = fitz.open(resolve_source(path))
        pages = doc.page_count
        doc.close()
        return pages
    except Exception:
        return 0


def estimate_machine(machine: dict, *, count_pages: bool = False) -> dict:
    files = machine.get("files", {})
    pdfs = files.get("pdf", [])
    images = files.get("image", [])
    texts = files.get("text", [])
    pages = sum(_pdf_pages(f["path"]) for f in pdfs) if count_pages else 0
    fallback_pages = len(pdfs) * 100
    weight = max(pages, fallback_pages) + len(images) * 5 + len(texts)
    return {
        "include_path": machine.get("rel_path") or machine["folder"],
        "folder": machine["folder"],
        "customer": machine.get("customer"),
        "slug": machine["slug"],
        "pdfs": len(pdfs),
        "pages": pages,
        "images": len(images),
        "texts": len(texts),
        "weight": weight,
    }


def plan_shards(manifest: dict, *, shard_count: int, count_pages: bool = False) -> dict:
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")

    machines = [estimate_machine(m, count_pages=count_pages) for m in manifest.get("machines", [])]
    shards = [
        {
            "name": f"shard-{i:02d}",
            "include_paths": [],
            "machines": [],
            "pdfs": 0,
            "pages": 0,
            "images": 0,
            "texts": 0,
            "weight": 0,
        }
        for i in range(1, shard_count + 1)
    ]

    for machine in sorted(machines, key=lambda m: m["weight"], reverse=True):
        shard = min(shards, key=lambda s: s["weight"])
        shard["include_paths"].append(machine["include_path"])
        shard["machines"].append(machine)
        shard["pdfs"] += machine["pdfs"]
        shard["pages"] += machine["pages"]
        shard["images"] += machine["images"]
        shard["texts"] += machine["texts"]
        shard["weight"] += machine["weight"]

    return {
        "summary": {
            "shard_count": shard_count,
            "machine_count": len(machines),
            "pdfs": sum(m["pdfs"] for m in machines),
            "pages": sum(m["pages"] for m in machines),
            "images": sum(m["images"] for m in machines),
            "texts": sum(m["texts"] for m in machines),
            "weight": sum(m["weight"] for m in machines),
            "count_pages": count_pages,
        },
        "shards": shards,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-path", default=PROTO_MANIFEST_PATH)
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--count-pages", action="store_true")
    parser.add_argument("--output", help="Write JSON plan to this path instead of stdout")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest_path).read_text())
    plan = plan_shards(manifest, shard_count=args.shards, count_pages=args.count_pages)
    payload = json.dumps(plan, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(payload)
    else:
        print(payload)


if __name__ == "__main__":
    main()
