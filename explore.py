"""
Gramag ERP Data Explorer
Analyzes the implicit knowledge graph in the ERP data.
"""
import csv
import os
from collections import Counter, defaultdict
from itertools import combinations

DATA = "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/ERP"

def load_csv(name, max_rows=None):
    path = os.path.join(DATA, name)
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for i, row in enumerate(reader):
            if max_rows and i >= max_rows:
                break
            rows.append(row)
    return rows

def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ── 1. MACHINES (produkte) ──────────────────────────────────────────────

section("1. MACHINE PARK — What machines does Gramag service?")

produkte = load_csv("produkte.csv")
print(f"Total machines/products registered: {len(produkte)}")

# Machine types from optional fields
machine_types = Counter()
machine_families = Counter()
for p in produkte:
    t1 = p.get("optional1", "").strip()
    t2 = p.get("optional2", "").strip()
    if t1 and t1 not in ("leer", ""):
        machine_types[t1] += 1
    if t2 and t2 not in ("leer", ""):
        machine_families[t2] += 1

print(f"\nMachine types (optional1) — {len(machine_types)} distinct:")
for mt, c in machine_types.most_common(25):
    print(f"  {c:>5}x  {mt}")

print(f"\nMachine families (optional2) — {len(machine_families)} distinct:")
for mf, c in machine_families.most_common(25):
    print(f"  {c:>5}x  {mf}")

# Customers with most machines
kunde_machines = Counter()
for p in produkte:
    kid = p.get("ref_kunde", "").strip()
    if kid and kid != "0":
        kunde_machines[kid] += 1

print(f"\nCustomers with machines: {len(kunde_machines)}")
print("Top 10 customers by machine count:")
kunden = {k["id"]: k for k in load_csv("kunden.csv")}
adressen = {a["id"]: a for a in load_csv("adressen.csv")}
for kid, c in kunde_machines.most_common(10):
    kname = ""
    if kid in kunden:
        uid = kunden[kid].get("uid", "")
        if uid in adressen:
            kname = adressen[uid].get("firmenname", "") or adressen[uid].get("suchname", "")
    print(f"  {c:>5}x  [{kid}] {kname}")


# ── 2. ARTICLES / SPARE PARTS ──────────────────────────────────────────

section("2. SPARE PARTS — What parts does Gramag sell/use?")

artikel = load_csv("artikel.csv")
print(f"Total articles/parts: {len(artikel)}")

# Group by artikel.gruppe
art_groups = Counter()
for a in artikel:
    g = a.get("gruppe", "").strip()
    if g:
        art_groups[g] += 1
print(f"\nArticle groups: {len(art_groups)} distinct")
for g, c in art_groups.most_common(15):
    print(f"  {c:>5}x  Group {g}")

# Stock status
active_stock = sum(1 for a in artikel if a.get("lagerAktiv") == "1")
print(f"\nArticles with stock tracking: {active_stock}")

# Articles with manufacturer numbers (identifiable parts)
with_mfr = sum(1 for a in artikel if a.get("herstellerNr", "").strip())
print(f"Articles with manufacturer number: {with_mfr}")

# Sample article titles to understand part types
print("\nSample article titles (first 20):")
for a in artikel[:20]:
    title = a.get("lang1Titel", "").strip()
    nr = a.get("nummer", "")
    mfr = a.get("herstellerNr", "")
    if title:
        print(f"  [{nr}] {title[:80]}  (mfr: {mfr})")


# ── 3. SERVICE JOBS / DOCUMENTS ─────────────────────────────────────────

section("3. SERVICE JOBS — What work does Gramag do?")

dokumente = load_csv("dokumente.csv")
print(f"Total documents (quotes/orders/invoices): {len(dokumente)}")

# Document types
dok_types = Counter()
for d in dokumente:
    dok_types[d.get("typ", "?")] += 1
print("\nDocument types:")
for t, c in dok_types.most_common():
    label = {"a": "Angebot (Quote)", "b": "Bestellung (Order)", "r": "Rechnung (Invoice)",
             "s": "Serviceauftrag (Service)", "g": "Gutschrift (Credit)", "l": "Lieferschein (Delivery)"}.get(t, t)
    print(f"  {c:>5}x  {t} = {label}")

# Document status
dok_status = Counter()
for d in dokumente:
    dok_status[d.get("svStatus", "?")] += 1
print("\nDocument statuses:")
for s, c in dok_status.most_common(10):
    print(f"  {c:>5}x  {s}")

# Documents linked to machines (ref_produkt)
docs_with_machine = sum(1 for d in dokumente if d.get("ref_produkt", "0") not in ("0", ""))
print(f"\nDocuments linked to a machine: {docs_with_machine} / {len(dokumente)}")

# Revenue analysis
revenues = []
for d in dokumente:
    try:
        amt = float(d.get("preisBrutto", "0").replace(",", "."))
        if amt > 0:
            revenues.append(amt)
    except:
        pass
if revenues:
    print(f"\nRevenue from {len(revenues)} priced documents:")
    print(f"  Total:   CHF {sum(revenues):>14,.2f}")
    print(f"  Average: CHF {sum(revenues)/len(revenues):>14,.2f}")
    print(f"  Median:  CHF {sorted(revenues)[len(revenues)//2]:>14,.2f}")
    print(f"  Max:     CHF {max(revenues):>14,.2f}")

# Categories
kategorien = {k["id"]: k.get("beschreibung1", "") for k in load_csv("kategorien.csv")}
dok_cats = Counter()
for d in dokumente:
    cat_id = d.get("ref_kategorie", "")
    cat_name = kategorien.get(cat_id, f"ID:{cat_id}")
    dok_cats[cat_name] += 1
print("\nDocument categories:")
for cat, c in dok_cats.most_common(15):
    print(f"  {c:>5}x  {cat}")


# ── 4. THE IMPLICIT GRAPH: Parts ↔ Jobs ↔ Machines ─────────────────────

section("4. IMPLICIT KNOWLEDGE GRAPH — Parts used in Jobs for Machines")

dok_artikel = load_csv("dok_artikel.csv")
print(f"Total line items (part-in-job links): {len(dok_artikel)}")

# Build lookup: dok_id → produkt_id (machine)
dok_to_machine = {}
dok_to_kunde = {}
for d in dokumente:
    did = d["id"]
    pid = d.get("ref_produkt", "0")
    kid = d.get("ref_kunde", "0")
    if pid and pid != "0":
        dok_to_machine[did] = pid
    if kid and kid != "0":
        dok_to_kunde[did] = kid

# Build: artikel_id → machine_ids (which parts serve which machines)
artikel_to_machines = defaultdict(set)
machine_to_artikels = defaultdict(set)
artikel_lookup = {a["id"]: a for a in artikel}
produkt_lookup = {p["id"]: p for p in produkte}

for da in dok_artikel:
    dok_id = da.get("ref_dok", "")
    art_id = da.get("ref_art", "")
    if dok_id in dok_to_machine and art_id:
        machine_id = dok_to_machine[dok_id]
        artikel_to_machines[art_id].add(machine_id)
        machine_to_artikels[machine_id].add(art_id)

print(f"\nParts linked to at least one machine: {len(artikel_to_machines)}")
print(f"Machines linked to at least one part:  {len(machine_to_artikels)}")

# Most-used parts (across different machines)
print("\nTop 20 parts used across most DIFFERENT machines:")
for art_id, machines in sorted(artikel_to_machines.items(), key=lambda x: len(x[1]), reverse=True)[:20]:
    art = artikel_lookup.get(art_id, {})
    title = art.get("lang1Titel", "?")[:60]
    nr = art.get("nummer", "?")
    print(f"  {len(machines):>4} machines  [{nr}] {title}")

# Machines requiring most different parts
print("\nTop 20 machines requiring most different parts:")
for mach_id, parts in sorted(machine_to_artikels.items(), key=lambda x: len(x[1]), reverse=True)[:20]:
    prod = produkt_lookup.get(mach_id, {})
    title = prod.get("titel", "?")[:60]
    print(f"  {len(parts):>4} parts  {title}")


# ── 5. PART CO-OCCURRENCE (which parts are used together?) ──────────────

section("5. PART CO-OCCURRENCE — Which parts are ordered together?")

# Group parts by document (job)
dok_parts = defaultdict(set)
for da in dok_artikel:
    dok_id = da.get("ref_dok", "")
    art_id = da.get("ref_art", "")
    if dok_id and art_id:
        dok_parts[dok_id].add(art_id)

# Count co-occurrences (parts appearing in the same job)
pair_counts = Counter()
for dok_id, parts in dok_parts.items():
    if 2 <= len(parts) <= 30:  # skip single-item and huge orders
        for a, b in combinations(sorted(parts), 2):
            pair_counts[(a, b)] += 1

print(f"Total unique part pairs found: {len(pair_counts)}")
print("\nTop 25 most frequently co-ordered part pairs:")
for (a, b), count in pair_counts.most_common(25):
    a_name = artikel_lookup.get(a, {}).get("lang1Titel", "?")[:40]
    b_name = artikel_lookup.get(b, {}).get("lang1Titel", "?")[:40]
    a_nr = artikel_lookup.get(a, {}).get("nummer", "?")
    b_nr = artikel_lookup.get(b, {}).get("nummer", "?")
    print(f"  {count:>4}x  [{a_nr}] {a_name}  +  [{b_nr}] {b_name}")


# ── 6. CUSTOMER PATTERNS ────────────────────────────────────────────────

section("6. CUSTOMER PATTERNS — Who are the key customers?")

kunde_docs = Counter()
kunde_revenue = defaultdict(float)
for d in dokumente:
    kid = d.get("ref_kunde", "0")
    if kid != "0":
        kunde_docs[kid] += 1
        try:
            kunde_revenue[kid] += float(d.get("preisBrutto", "0").replace(",", "."))
        except:
            pass

print("Top 15 customers by number of documents:")
for kid, c in kunde_docs.most_common(15):
    kname = ""
    if kid in kunden:
        uid = kunden[kid].get("uid", "")
        if uid in adressen:
            kname = adressen[uid].get("firmenname", "") or adressen[uid].get("suchname", "")
    rev = kunde_revenue.get(kid, 0)
    print(f"  {c:>5} docs  CHF {rev:>12,.2f}  {kname}")


# ── 7. SERVICE NOTES / COMMENTS (RAG potential) ────────────────────────

section("7. SERVICE NOTES — Free text knowledge (RAG potential)")

kommentare = load_csv("kommentare.csv")
print(f"Total comments: {len(kommentare)}")

# Types of commented entities
comment_types = Counter()
for k in kommentare:
    comment_types[k.get("ref_typ", "?")] += 1
print("\nComments by entity type:")
for t, c in comment_types.most_common():
    print(f"  {c:>5}x  {t}")

# Average comment length
lengths = [len(k.get("kommentar", "")) for k in kommentare]
print(f"\nComment lengths: avg {sum(lengths)//len(lengths)} chars, max {max(lengths)} chars")
total_text = sum(lengths)
print(f"Total comment text: ~{total_text//1000} KB")

# Sample comments
print("\nSample service comments (first 5 on 'dok' type):")
shown = 0
for k in kommentare:
    if k.get("ref_typ") == "dok" and len(k.get("kommentar", "")) > 50 and shown < 5:
        comment = k["kommentar"][:200].replace("<br>", " ").replace("\n", " ")
        # Strip HTML
        import re
        comment = re.sub(r'<[^>]+>', '', comment)
        print(f"  - {comment}")
        shown += 1


# ── 8. EMAILS (additional RAG source) ──────────────────────────────────

section("8. EMAILS — Communication knowledge")

emails = load_csv("emails.csv", max_rows=5)
if emails:
    print(f"Email fields: {list(emails[0].keys())[:15]}")
    email_count = 0
    with open(os.path.join(DATA, "emails.csv"), "r", encoding="utf-8-sig") as f:
        for _ in f:
            email_count += 1
    print(f"Total emails: {email_count - 1}")


# ── 9. SUPPLIER DOCUMENTATION ──────────────────────────────────────────

section("9. SUPPLIER DOCUMENTATION — Technical manuals")

service_root = "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/Servicedaten/Lieferanten"
if os.path.exists(service_root):
    suppliers = sorted([d for d in os.listdir(service_root) if os.path.isdir(os.path.join(service_root, d))])
    print(f"Suppliers with documentation: {len(suppliers)}")
    for s in suppliers:
        spath = os.path.join(service_root, s)
        pdf_count = sum(1 for _, _, files in os.walk(spath) for f in files if f.lower().endswith('.pdf'))
        total_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, files in os.walk(spath) for f in files)
        print(f"  {s:<30}  {pdf_count:>4} PDFs  {total_size/1024/1024:>8.1f} MB")


# ── 10. SUMMARY ─────────────────────────────────────────────────────────

section("SUMMARY — Knowledge Graph Potential")

print(f"""
Data volumes:
  Machines registered:      {len(produkte):>8,}
  Spare parts catalog:      {len(artikel):>8,}
  Service documents:        {len(dokumente):>8,}
  Part-in-job line items:   {len(dok_artikel):>8,}
  Service comments:         {len(kommentare):>8,}

Implicit graph edges:
  Part → Machine links:     {sum(len(v) for v in artikel_to_machines.values()):>8,}
  Part co-occurrence pairs: {len(pair_counts):>8,}

This data contains a rich implicit knowledge graph:
  - Machine → needs Parts (from service history)
  - Part → used with Parts (co-occurrence)
  - Customer → owns Machines
  - Machine → had Problems (from comments/notes)
  - Problem → solved with Parts (from job line items)

Combined with 1.7 GB supplier PDFs, this is strong foundation
for a Knowledge Graph + RAG system.
""")
