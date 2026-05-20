"""Validate ERP CSV export before import into FalkorDB.

Usage:
    python validate_erp_import.py --data-dir ~/Downloads/GramagOutput

Exit code: 0 = all checks pass, 1 = any failure.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

# Required files and their mandatory columns
REQUIRED_FILES: dict[str, list[str]] = {
    "kunden.csv":      ["id", "nummer"],
    "produkte.csv":    ["id", "titel", "ref_kunde"],
    "artikel.csv":     ["id", "lang1Titel", "nummer"],
    "dokumente.csv":   ["id", "nummer", "typ", "ref_produkt", "ref_kunde"],
    "dok_artikel.csv": ["id", "ref_dok", "ref_art"],
    "kommentare.csv":  ["id", "ref_typ", "ref_id", "kommentar"],
    "adressen.csv":    ["id", "firmenname"],
}

PASS  = "\033[32mPASS\033[0m"
WARN  = "\033[33mWARN\033[0m"
FAIL  = "\033[31mFAIL\033[0m"


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    status = PASS if ok else (WARN if warn_only else FAIL)
    line = f"  [{status}] {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    return ok or warn_only


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def validate(data_dir: str) -> bool:
    data_dir = os.path.expanduser(data_dir)
    print(f"\n{'='*60}")
    print(f"  ERP Import Validation")
    print(f"  Directory: {data_dir}")
    print(f"{'='*60}\n")

    all_pass = True

    # 1. Directory exists
    if not check("Directory exists", os.path.isdir(data_dir), data_dir):
        print(f"\n[FAIL] Cannot proceed — directory not found.\n")
        return False

    # 2. Per-file checks
    loaded: dict[str, list[dict]] = {}
    print("File checks:")
    for filename, required_cols in REQUIRED_FILES.items():
        path = os.path.join(data_dir, filename)

        if not os.path.exists(path):
            all_pass = False
            check(f"{filename} exists", False)
            continue

        check(f"{filename} exists", True)

        try:
            rows = load_csv(path)
        except Exception as e:
            all_pass = False
            check(f"{filename} parses", False, str(e))
            continue

        check(f"{filename} parses", True, f"{len(rows):,} rows")

        if not rows:
            all_pass = False
            check(f"{filename} non-empty", False)
            continue

        # Column presence
        actual_cols = set(rows[0].keys())
        missing = [c for c in required_cols if c not in actual_cols]
        if missing:
            all_pass = False
            check(f"{filename} required columns", False, f"missing: {missing}")
        else:
            check(f"{filename} required columns", True)

        # Null primary keys
        null_ids = sum(1 for r in rows if not r.get("id", "").strip())
        if null_ids:
            all_pass = all_pass and check(
                f"{filename} no null id", False,
                f"{null_ids} rows with empty id", warn_only=True
            )
        else:
            check(f"{filename} no null id", True)

        loaded[filename] = rows
        print()

    # 3. Cross-file / business checks
    print("Business checks:")

    if "dokumente.csv" in loaded:
        service_docs = [d for d in loaded["dokumente.csv"] if d.get("typ") == "s"]
        ok = len(service_docs) > 0
        if not ok:
            all_pass = False
        check(
            "Service documents (typ='s')",
            ok,
            f"{len(service_docs):,} of {len(loaded['dokumente.csv']):,} total documents",
        )

    if "dok_artikel.csv" in loaded and "dokumente.csv" in loaded:
        service_ids = {d["id"] for d in loaded["dokumente.csv"] if d.get("typ") == "s"}
        linked = sum(1 for da in loaded["dok_artikel.csv"] if da.get("ref_dok") in service_ids)
        check(
            "dok_artikel linked to service docs",
            linked > 0,
            f"{linked:,} of {len(loaded['dok_artikel.csv']):,} rows",
        )

    if "kommentare.csv" in loaded:
        dok_comments = [k for k in loaded["kommentare.csv"] if k.get("ref_typ") == "dok"]
        check(
            "Comments on documents (ref_typ='dok')",
            len(dok_comments) > 0,
            f"{len(dok_comments):,} of {len(loaded['kommentare.csv']):,} total comments",
        )

    if "produkte.csv" in loaded:
        with_sn = sum(1 for p in loaded["produkte.csv"] if p.get("seriennummer", "").strip())
        check(
            "Machines with serial number",
            with_sn > 0,
            f"{with_sn:,} of {len(loaded['produkte.csv']):,}",
            warn_only=True,
        )

    # 4. Summary
    print(f"\n{'='*60}")
    if all_pass:
        print(f"  [{PASS}] All checks passed — ready to import.")
    else:
        print(f"  [{FAIL}] One or more checks failed — fix before importing.")
    print(f"{'='*60}\n")

    return all_pass


def main() -> int:
    p = argparse.ArgumentParser(description="Validate ERP CSV export before FalkorDB import.")
    p.add_argument("--data-dir", required=True, help="Directory containing ERP CSV exports")
    args = p.parse_args()

    ok = validate(args.data_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
