"""Scan the Drive dump, parse machine names, classify files, emit manifest.json.

Run: python -m proto.scan
"""

import json
import os
import re
from collections import Counter
from pathlib import Path

from proto import PROTO_MANIFEST_PATH, PROTO_ROOT

PROTO_ROOT_MODE = os.getenv("PROTO_ROOT_MODE", "machines")

PDF_EXT = {".pdf"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".pcx"}
TXT_EXT = {".txt"}
DOC_EXT = {".doc", ".docx", ".xls", ".xlsx"}
CAD_EXT = {".dwg", ".dxf"}
WEB_EXT = {".htm", ".html", ".xml"}
# Hard-skip binaries / proprietary machine data — no value for KB
SKIP_EXT = {
    ".ijj", ".dat", ".pp0", ".ba0", ".evn", ".dll", ".drv", ".sys",
    ".exe", ".bin", ".job", ".cfd", ".f01", ".mmf", ".schema", ".ps",
    ".one", ".onetoc2", ".url", ".lnk", ".hlp", ".bat", ".inf", ".js",
}

IGNORE_NAMES = {".DS_Store", "Thumbs.db"}


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in SKIP_EXT:
        return "skip"
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMG_EXT:
        return "image"
    if ext in TXT_EXT:
        return "text"
    if ext in DOC_EXT:
        return "office"
    if ext in CAD_EXT:
        return "cad"
    if ext in WEB_EXT:
        return "web"
    return "other"


MACHINE_NAME_RE = re.compile(
    r"""^
        (?P<type>[A-Za-zÄÖÜäöüß\-\.&\s/]+?)
        (?:\s{2,}|\s+-\s+)
        (?P<model>[A-Za-z0-9\-\.\s/]+?)
        (?:\s{2,}|\s+-\s+)
        (?P<serial>(?:Nr\s+)?[A-Za-z0-9\-\s\.]+)
        \s*$
    """,
    re.VERBOSE,
)


def parse_machine_name(folder: str) -> dict:
    """Parse 'Falzmaschine   T800-6-R   Nr 59 99 03 04' into components."""
    raw = folder.strip()
    m = MACHINE_NAME_RE.match(raw)
    if m:
        return {
            "raw": raw,
            "type": m.group("type").strip(),
            "model": m.group("model").strip(),
            "serial": re.sub(r"^Nr\s+", "", m.group("serial").strip()),
        }
    # Fallback: split on 2+ spaces
    parts = re.split(r"\s{2,}", raw)
    if len(parts) >= 2:
        return {
            "raw": raw,
            "type": parts[0].strip(),
            "model": parts[1].strip() if len(parts) > 1 else None,
            "serial": re.sub(r"^Nr\s+", "", parts[-1].strip()) if len(parts) > 2 else None,
        }
    return {"raw": raw, "type": raw, "model": None, "serial": None}


def walk_machine(machine_dir: Path) -> dict:
    """Walk a machine folder and classify all files."""
    files_by_kind: dict[str, list] = {
        "pdf": [], "image": [], "text": [], "office": [],
        "cad": [], "web": [], "other": [], "skip": [],
    }
    categories: Counter = Counter()
    total_size = 0

    for root, dirs, files in os.walk(machine_dir):
        dirs[:] = [d for d in dirs if d not in IGNORE_NAMES and not d.startswith(".")]
        for name in files:
            if name in IGNORE_NAMES or name.startswith("."):
                continue
            p = Path(root) / name
            try:
                stat = p.stat()
                size = stat.st_size
                mtime = int(stat.st_mtime)
            except OSError:
                size = 0
                mtime = 0
            total_size += size
            kind = classify(p)
            rel = p.relative_to(machine_dir)
            # category = first-level subdir if any, else "_root"
            parts = rel.parts
            category = parts[0] if len(parts) > 1 else "_root"
            categories[category] += 1
            files_by_kind[kind].append({
                "path": str(p),
                "rel": str(rel),
                "category": category,
                "size": size,
                "mtime": mtime,
                "name": name,
            })

    return {
        "files": files_by_kind,
        "counts": {k: len(v) for k, v in files_by_kind.items()},
        "categories": dict(categories),
        "total_size": total_size,
    }


def has_ingestible_content(walk: dict) -> bool:
    counts = walk["counts"]
    return counts["pdf"] + counts["image"] + counts["text"] > 0


def machine_entry(machine_dir: Path, root_path: Path, customer: str | None = None) -> dict | None:
    parsed = parse_machine_name(machine_dir.name)
    walk = walk_machine(machine_dir)
    if not has_ingestible_content(walk):
        return None
    rel_path = machine_dir.relative_to(root_path)
    return {
        "folder": machine_dir.name,
        "customer": customer,
        "customer_folder": customer,
        "path": str(machine_dir),
        "rel_path": str(rel_path),
        "slug": re.sub(r"[^a-zA-Z0-9]+", "-", str(rel_path)).strip("-").lower(),
        **parsed,
        **walk,
    }


def iter_machine_entries(root_path: Path, root_mode: str, customer_name: str | None = None) -> list[dict]:
    machines = []
    if root_mode not in {"machines", "customers"}:
        raise ValueError("root_mode must be 'machines' or 'customers'")

    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if root_mode == "machines":
            machine = machine_entry(entry, root_path, customer=customer_name)
            if machine:
                machines.append(machine)
            continue

        customer = entry.name
        for machine_dir in sorted(entry.iterdir()):
            if not machine_dir.is_dir() or machine_dir.name.startswith("."):
                continue
            machine = machine_entry(machine_dir, root_path, customer=customer)
            if machine:
                machines.append(machine)
    return machines


def build_manifest(
    root: str = PROTO_ROOT,
    root_mode: str = PROTO_ROOT_MODE,
    customer_name: str | None = None,
) -> dict:
    root_path = Path(root)
    machines = iter_machine_entries(root_path, root_mode, customer_name=customer_name)

    # Top-level files (not inside a machine folder)
    top_level = []
    for entry in root_path.iterdir():
        if entry.is_file() and entry.name not in IGNORE_NAMES and not entry.name.startswith("."):
            top_level.append({
                "path": str(entry),
                "name": entry.name,
                "kind": classify(entry),
                "size": entry.stat().st_size,
            })

    summary = {
        "machine_count": len(machines),
        "total_pdfs": sum(m["counts"]["pdf"] for m in machines),
        "total_images": sum(m["counts"]["image"] for m in machines),
        "total_texts": sum(m["counts"]["text"] for m in machines),
        "total_office": sum(m["counts"]["office"] for m in machines),
        "total_cad": sum(m["counts"]["cad"] for m in machines),
        "total_web": sum(m["counts"]["web"] for m in machines),
        "total_skipped": sum(m["counts"]["skip"] for m in machines),
        "total_bytes": sum(m["total_size"] for m in machines),
    }

    return {
        "root": root,
        "root_mode": root_mode,
        "customer_name": customer_name,
        "summary": summary,
        "machines": machines,
        "top_level_files": top_level,
    }


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=PROTO_ROOT)
    ap.add_argument("--manifest-path", default=PROTO_MANIFEST_PATH)
    ap.add_argument("--root-mode", choices=["machines", "customers"], default=PROTO_ROOT_MODE)
    ap.add_argument("--customer-name", default=os.getenv("PROTO_CUSTOMER_NAME"))
    args = ap.parse_args()

    manifest = build_manifest(args.root, root_mode=args.root_mode, customer_name=args.customer_name)
    with open(args.manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    s = manifest["summary"]
    print(f"Machines:   {s['machine_count']}")
    print(f"Root mode:  {manifest['root_mode']}")
    print(f"PDFs:       {s['total_pdfs']}")
    print(f"Images:     {s['total_images']}")
    print(f"Text files: {s['total_texts']}")
    print(f"Office:     {s['total_office']}")
    print(f"Total size: {s['total_bytes'] / 1e6:.1f} MB")
    print()
    print("Per-machine breakdown:")
    for m in manifest["machines"]:
        c = m["counts"]
        print(f"  {m['folder']}")
        print(f"    type={m['type']!r} model={m['model']!r} serial={m['serial']!r}")
        print(f"    pdfs={c['pdf']} imgs={c['image']} txt={c['text']} "
              f"office={c['office']} cad={c['cad']} web={c['web']} "
              f"other={c['other']} skip={c['skip']} size={m['total_size']/1e6:.1f}MB")
    print()
    print(f"Manifest -> {args.manifest_path}")


if __name__ == "__main__":
    main()
