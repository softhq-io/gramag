"""Scan the Drive dump, parse machine names, classify files, emit manifest.json.

Run: python -m proto.scan
"""

import json
import os
import re
from collections import Counter
from pathlib import Path

from proto import PROTO_MANIFEST_PATH, PROTO_ROOT

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
                size = p.stat().st_size
            except OSError:
                size = 0
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
                "name": name,
            })

    return {
        "files": files_by_kind,
        "counts": {k: len(v) for k, v in files_by_kind.items()},
        "categories": dict(categories),
        "total_size": total_size,
    }


def build_manifest(root: str = PROTO_ROOT) -> dict:
    root_path = Path(root)
    machines = []

    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        parsed = parse_machine_name(entry.name)
        walk = walk_machine(entry)
        machines.append({
            "folder": entry.name,
            "path": str(entry),
            "slug": re.sub(r"[^a-zA-Z0-9]+", "-", entry.name).strip("-").lower(),
            **parsed,
            **walk,
        })

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
        "summary": summary,
        "machines": machines,
        "top_level_files": top_level,
    }


def main():
    manifest = build_manifest()
    with open(PROTO_MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    s = manifest["summary"]
    print(f"Machines:   {s['machine_count']}")
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
    print(f"Manifest -> {PROTO_MANIFEST_PATH}")


if __name__ == "__main__":
    main()
