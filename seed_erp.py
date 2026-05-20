"""Gramag Knowledge Graph — Seed ERP data into FalkorDB.

Reads CSV exports from ERP (kunden, produkte, artikel, dokumente, dok_artikel,
kommentare, adressen) and creates the L1 Inventory + L2.5 Service History layers.

All writes use MERGE for idempotent re-runs.
"""

import argparse
import csv
import os
import re
import time
from config import ERP_DIR, NOISE_KEYWORDS


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed ERP CSV exports into FalkorDB.")
    p.add_argument(
        "--data-dir",
        default=ERP_DIR,
        help="Directory containing ERP CSV exports (default: config.ERP_DIR)",
    )
    return p.parse_args()


_args = parse_args()
ERP_DIR = _args.data_dir


def clean_html(text: str) -> str:
    """Strip HTML tags, replacing block elements with spaces to avoid word merging."""
    if not text:
        return ""
    # Block tags → space
    text = re.sub(r'<(?:br|p|div|li|tr|td|th|h\d)[\s/>][^>]*>', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|li|tr|td|th|h\d)>', ' ', text, flags=re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Normalize whitespace (including non-breaking spaces)
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
from db import db
from db_helpers import result_value


def load_csv(name: str) -> list[dict]:
    path = os.path.join(ERP_DIR, name)
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def is_noise(article: dict) -> bool:
    title = (article.get("lang1Titel", "") or "").lower()
    for kw in NOISE_KEYWORDS:
        if kw in title:
            return True
    return False


def parse_machine_title(title: str) -> tuple[str, str, str]:
    """Parse 'Falzmaschine   MBO T52   75072732' into (type, brand, model).

    Returns (machine_type, brand, rest) where rest might include model number.
    """
    parts = [x.strip() for x in title.split("   ") if x.strip()]
    machine_type = parts[0] if parts else title
    brand_model = parts[1] if len(parts) > 1 else ""
    brand = brand_model.split()[0] if brand_model else ""
    return machine_type, brand, brand_model


def batch_write(cypher: str, rows: list[dict], batch_size: int = 500, label: str = ""):
    """Write rows in batches using UNWIND."""
    total = len(rows)
    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        db.write(cypher, {"rows": batch})
        done = min(i + batch_size, total)
        if label:
            print(f"  {label}: {done}/{total}")


def seed_customers():
    """Create Customer nodes from kunden.csv + adressen.csv for city/name."""
    print("\n=== Customers ===")
    kunden = load_csv("kunden.csv")
    adressen = load_csv("adressen.csv")

    # Build address lookup: id -> {firmenname, ort, ...}
    addr_lookup = {}
    for a in adressen:
        addr_lookup[a["id"]] = a

    # kunden.hauptkontakt -> kontakte.ref_adresse, but simpler:
    # kunden.id maps to adressen via adressen_zuweis or direct match
    # Actually kunden.id IS the address id in this ERP (kunden.id == adressen.id for the primary)
    # Let's use the address that matches the customer id
    rows = []
    for k in kunden:
        kid = k["id"]
        addr = addr_lookup.get(kid, {})
        name = addr.get("firmenname", "").strip() or addr.get("suchname", "").strip()
        city = addr.get("ort", "").strip()
        if not name:
            name = f"Kunde {k.get('nummer', kid)}"
        rows.append({
            "erp_id": kid,
            "name": name,
            "city": city,
            "nummer": k.get("nummer", ""),
        })

    cypher = """
    UNWIND $rows AS r
    MERGE (c:Customer {erp_id: r.erp_id})
    SET c.name = r.name, c.city = r.city, c.nummer = r.nummer
    """
    batch_write(cypher, rows, label="Customers")
    print(f"  Total: {len(rows)}")


def seed_machines():
    """Create Machine, MachineType, MachineBrand nodes + relationships."""
    print("\n=== Machines ===")
    produkte = load_csv("produkte.csv")

    rows = []
    for p in produkte:
        title = p.get("titel", "").strip()
        if not title:
            continue
        sn = p.get("seriennummer", "").strip()
        ref_kunde = p.get("ref_kunde", "0")
        machine_type, brand, brand_model = parse_machine_title(title)

        rows.append({
            "erp_id": p["id"],
            "title": title,
            "serial_number": sn,
            "ref_kunde": ref_kunde,
            "machine_type": machine_type,
            "brand": brand,
        })

    # Create Machine nodes
    cypher_machines = """
    UNWIND $rows AS r
    MERGE (m:Machine {erp_id: r.erp_id})
    SET m.title = r.title, m.serial_number = r.serial_number
    """
    batch_write(cypher_machines, rows, label="Machines")

    # Link Machine -> Customer (OWNS)
    cypher_owns = """
    UNWIND $rows AS r
    WITH r WHERE r.ref_kunde <> '0' AND r.ref_kunde <> ''
    MATCH (c:Customer {erp_id: r.ref_kunde})
    MATCH (m:Machine {erp_id: r.erp_id})
    MERGE (c)-[:OWNS]->(m)
    """
    batch_write(cypher_owns, rows, label="OWNS edges")

    # Create MachineType nodes + link
    types_seen = set()
    type_rows = []
    for r in rows:
        mt = r["machine_type"]
        if mt and mt not in types_seen:
            types_seen.add(mt)
            type_rows.append({"name": mt})

    if type_rows:
        db.write(
            "UNWIND $rows AS r MERGE (:MachineType {name: r.name})",
            {"rows": type_rows},
        )
        cypher_is_type = """
        UNWIND $rows AS r
        WITH r WHERE r.machine_type <> ''
        MATCH (m:Machine {erp_id: r.erp_id})
        MATCH (mt:MachineType {name: r.machine_type})
        MERGE (m)-[:IS_TYPE]->(mt)
        """
        batch_write(cypher_is_type, rows, label="IS_TYPE edges")

    # Create MachineBrand nodes + link
    brands_seen = set()
    brand_rows = []
    for r in rows:
        b = r["brand"]
        if b and len(b) > 1 and b not in brands_seen:
            brands_seen.add(b)
            brand_rows.append({"name": b})

    if brand_rows:
        db.write(
            "UNWIND $rows AS r MERGE (:MachineBrand {name: r.name})",
            {"rows": brand_rows},
        )
        cypher_made_by = """
        UNWIND $rows AS r
        WITH r WHERE r.brand <> '' AND size(r.brand) > 1
        MATCH (m:Machine {erp_id: r.erp_id})
        MATCH (mb:MachineBrand {name: r.brand})
        MERGE (m)-[:MADE_BY]->(mb)
        """
        batch_write(cypher_made_by, rows, label="MADE_BY edges")

    print(f"  Total machines: {len(rows)}")
    print(f"  Machine types: {len(type_rows)}")
    print(f"  Brands: {len(brand_rows)}")


def seed_parts():
    """Create Part nodes. Mark noise articles."""
    print("\n=== Parts ===")
    artikel = load_csv("artikel.csv")

    rows = []
    noise_count = 0
    for a in artikel:
        title = a.get("lang1Titel", "").strip()
        if not title:
            continue
        nr = a.get("nummer", "").strip()
        mfr = a.get("herstellerNr", "").strip()
        noise = is_noise(a)
        if noise:
            noise_count += 1
        rows.append({
            "erp_id": a["id"],
            "nummer": nr,
            "titel": title,
            "manufacturer_nr": mfr,
            "noise": noise,
        })

    cypher = """
    UNWIND $rows AS r
    MERGE (p:Part {erp_id: r.erp_id})
    SET p.nummer = r.nummer, p.titel = r.titel,
        p.manufacturer_nr = r.manufacturer_nr, p.noise = r.noise
    """
    batch_write(cypher, rows, label="Parts")
    print(f"  Total: {len(rows)} (noise: {noise_count})")


def seed_service_jobs():
    """Create ServiceJob nodes + link to Machine and Customer."""
    print("\n=== Service Jobs ===")
    dokumente = load_csv("dokumente.csv")

    # Filter to service documents only (typ='s')
    rows = []
    for d in dokumente:
        if d.get("typ") != "s":
            continue
        title = d.get("bezeichnung", "").strip()
        desc = d.get("beschreibung", "").strip()
        # Clean HTML from description
        if desc:
            desc = clean_html(desc)
        rows.append({
            "erp_id": d["id"],
            "nummer": d.get("nummer", ""),
            "title": title,
            "description": desc[:500] if desc else "",
            "date": d.get("dokDatum", ""),
            "ref_produkt": d.get("ref_produkt", "0"),
            "ref_kunde": d.get("ref_kunde", "0"),
        })

    # Create ServiceJob nodes
    cypher_jobs = """
    UNWIND $rows AS r
    MERGE (sj:ServiceJob {erp_id: r.erp_id})
    SET sj.nummer = r.nummer, sj.title = r.title,
        sj.description = r.description, sj.date = r.date
    """
    batch_write(cypher_jobs, rows, label="ServiceJobs")

    # Link to Machine
    cypher_for_machine = """
    UNWIND $rows AS r
    WITH r WHERE r.ref_produkt <> '0' AND r.ref_produkt <> ''
    MATCH (sj:ServiceJob {erp_id: r.erp_id})
    MATCH (m:Machine {erp_id: r.ref_produkt})
    MERGE (sj)-[:FOR_MACHINE]->(m)
    """
    batch_write(cypher_for_machine, rows, label="FOR_MACHINE edges")

    # Link to Customer
    cypher_for_customer = """
    UNWIND $rows AS r
    WITH r WHERE r.ref_kunde <> '0' AND r.ref_kunde <> ''
    MATCH (sj:ServiceJob {erp_id: r.erp_id})
    MATCH (c:Customer {erp_id: r.ref_kunde})
    MERGE (sj)-[:FOR_CUSTOMER]->(c)
    """
    batch_write(cypher_for_customer, rows, label="FOR_CUSTOMER edges")

    print(f"  Total: {len(rows)}")


def seed_dok_artikel():
    """Create USED_PART edges between ServiceJob and Part from dok_artikel.csv.

    This is the biggest table (~106K rows), batched in groups of 1000.
    """
    print("\n=== dok_artikel (ServiceJob -> Part) ===")
    dok_artikel = load_csv("dok_artikel.csv")

    # We need to know which dokumente are service jobs
    dokumente = load_csv("dokumente.csv")
    service_dok_ids = {d["id"] for d in dokumente if d.get("typ") == "s"}

    rows = []
    for da in dok_artikel:
        dok_id = da.get("ref_dok", "")
        art_id = da.get("ref_art", "")
        if not dok_id or not art_id or dok_id not in service_dok_ids:
            continue
        rows.append({
            "dok_id": dok_id,
            "art_id": art_id,
            "quantity": da.get("anzahl", "0"),
            "price": da.get("preis", "0"),
        })

    cypher = """
    UNWIND $rows AS r
    MATCH (sj:ServiceJob {erp_id: r.dok_id})
    MATCH (p:Part {erp_id: r.art_id})
    MERGE (sj)-[u:USED_PART]->(p)
    SET u.quantity = toFloat(r.quantity), u.price = toFloat(r.price)
    """
    batch_write(cypher, rows, batch_size=1000, label="USED_PART edges")
    print(f"  Total line items: {len(rows)}")


def seed_comments():
    """Create ServiceComment nodes linked to ServiceJobs."""
    print("\n=== Comments ===")
    kommentare = load_csv("kommentare.csv")

    rows = []
    for k in kommentare:
        ref_typ = k.get("ref_typ", "")
        ref_id = k.get("ref_id", "")
        text = k.get("kommentar", "").strip()
        if ref_typ != "dok" or not ref_id or len(text) < 20:
            continue
        # Clean HTML
        clean = clean_html(text)
        if len(clean) < 20:
            continue
        rows.append({
            "erp_id": k["id"],
            "text": clean[:1000],
            "ref_dok": ref_id,
            "date": k.get("datum", ""),
        })

    cypher = """
    UNWIND $rows AS r
    MERGE (sc:ServiceComment {erp_id: r.erp_id})
    SET sc.text = r.text, sc.date = r.date
    WITH sc, r
    MATCH (sj:ServiceJob {erp_id: r.ref_dok})
    MERGE (sc)-[:ON_JOB]->(sj)
    """
    batch_write(cypher, rows, label="Comments")
    print(f"  Total: {len(rows)}")


def verify():
    """Run verification queries."""
    print("\n=== Verification ===")
    stats = db.stats()
    print("  Nodes:")
    for label, count in sorted(stats["nodes"].items()):
        print(f"    {label}: {count:,}")
    print("  Relationships:")
    for rel, count in sorted(stats["relationships"].items()):
        print(f"    {rel}: {count:,}")

    # Sample traversal
    print("\n  Sample traversal (Customer -> Machine -> ServiceJob -> Part):")
    result = db.query("""
        MATCH (c:Customer)-[:OWNS]->(m:Machine)<-[:FOR_MACHINE]-(sj:ServiceJob)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        RETURN c.name AS customer, m.title AS machine, sj.title AS job, p.titel AS part
        LIMIT 5
    """)
    from db_helpers import result_to_dicts
    for row in result_to_dicts(result):
        print(f"    {row['customer'][:30]} | {row['machine'][:30]} | {row['job'][:30]} | {row['part'][:30]}")


def main():
    print("=" * 60)
    print("  Gramag Knowledge Graph — ERP Seed")
    print("=" * 60)

    t0 = time.time()

    # Ensure connection
    db.connect()
    print(f"Connected to FalkorDB ({db.host}:{db.port}/{db.graph_name})")

    # Apply schema first
    from schema import apply_indexes
    print("\n--- Applying indexes ---")
    apply_indexes()

    # Seed in order (dependencies matter)
    seed_customers()
    seed_machines()
    seed_parts()
    seed_service_jobs()
    seed_dok_artikel()
    seed_comments()

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  Seed completed in {elapsed:.1f}s")
    print(f"{'=' * 60}")

    verify()


if __name__ == "__main__":
    main()
