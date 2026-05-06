"""
Gramag Deep Analysis — Filter out noise, find real part-machine relationships.
"""
import csv
import os
import re
from collections import Counter, defaultdict
from itertools import combinations

DATA = "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten/ERP"

def load_csv(name):
    path = os.path.join(DATA, name)
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            rows.append(row)
    return rows

def section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

# Load core tables
artikel = load_csv("artikel.csv")
produkte = load_csv("produkte.csv")
dokumente = load_csv("dokumente.csv")
dok_artikel = load_csv("dok_artikel.csv")

artikel_lookup = {a["id"]: a for a in artikel}
produkt_lookup = {p["id"]: p for p in produkte}

# ── Identify noise articles (shipping, travel, service hours) ───────────
NOISE_KEYWORDS = [
    "porto", "verpackung", "spedition", "camion", "luftpolster",
    "technikerstunden", "fahrzeit", "google maps", "kulanz",
    "pauschale verpflegung", "mittagessen", "nachtessen", "hotel",
    "reisespesen", "parkplatz", "maut", "holzbox",
]

def is_noise(art):
    title = (art.get("lang1Titel", "") or art.get("titel", "")).lower()
    nr = art.get("nummer", "")
    # Service/travel articles typically have low numbers (10xxx, 22xxx range)
    for kw in NOISE_KEYWORDS:
        if kw in title:
            return True
    return False

noise_ids = set()
real_parts = {}
for a in artikel:
    if is_noise(a):
        noise_ids.add(a["id"])
    else:
        real_parts[a["id"]] = a

print(f"Total articles: {len(artikel)}")
print(f"Noise articles filtered: {len(noise_ids)}")
print(f"Real spare parts: {len(real_parts)}")


# ── 1. Machine types from titles ────────────────────────────────────────

section("1. MACHINE TYPES — Extracted from product titles")

# Parse machine brand/model from titel field
machine_brands = Counter()
for p in produkte:
    title = p.get("titel", "")
    # Common patterns: "Falzmaschine   MBO T52   75072732"
    #                  "Folieranlage   CMCone   1000154"
    parts = [x.strip() for x in title.split("   ") if x.strip()]
    if len(parts) >= 2:
        machine_type = parts[0]  # e.g. "Falzmaschine"
        brand_model = parts[1] if len(parts) > 1 else ""
        machine_brands[machine_type] += 1

print(f"Machine categories found: {len(machine_brands)}")
for mt, c in machine_brands.most_common(30):
    print(f"  {c:>5}x  {mt}")

# Extract brand names
brands = Counter()
for p in produkte:
    title = p.get("titel", "")
    parts = [x.strip() for x in title.split("   ") if x.strip()]
    if len(parts) >= 2:
        brand = parts[1].split()[0] if parts[1] else ""
        if brand and len(brand) > 1:
            brands[brand] += 1

print(f"\nMachine brands: {len(brands)} distinct")
for b, c in brands.most_common(25):
    print(f"  {c:>5}x  {b}")


# ── 2. Real part co-occurrence (excluding noise) ────────────────────────

section("2. REAL PART CO-OCCURRENCE — Actual spare parts ordered together")

dok_parts = defaultdict(set)
for da in dok_artikel:
    dok_id = da.get("ref_dok", "")
    art_id = da.get("ref_art", "")
    if dok_id and art_id and art_id not in noise_ids:
        dok_parts[dok_id].add(art_id)

pair_counts = Counter()
for dok_id, parts in dok_parts.items():
    if 2 <= len(parts) <= 20:
        for a, b in combinations(sorted(parts), 2):
            pair_counts[(a, b)] += 1

print(f"Unique real part pairs: {len(pair_counts)}")
print("\nTop 30 most frequently co-ordered REAL part pairs:")
for (a, b), count in pair_counts.most_common(30):
    a_art = artikel_lookup.get(a, {})
    b_art = artikel_lookup.get(b, {})
    a_name = a_art.get("lang1Titel", "?")[:45]
    b_name = b_art.get("lang1Titel", "?")[:45]
    a_nr = a_art.get("nummer", "?")
    b_nr = b_art.get("nummer", "?")
    print(f"  {count:>3}x  [{a_nr}] {a_name}")
    print(f"       [{b_nr}] {b_name}")
    print()


# ── 3. Machine → Part affinity (which parts go with which machines?) ───

section("3. MACHINE → PART AFFINITY — Service history patterns")

dok_to_machine = {}
for d in dokumente:
    pid = d.get("ref_produkt", "0")
    if pid and pid != "0":
        dok_to_machine[d["id"]] = pid

machine_parts = defaultdict(lambda: Counter())
for da in dok_artikel:
    dok_id = da.get("ref_dok", "")
    art_id = da.get("ref_art", "")
    if dok_id in dok_to_machine and art_id and art_id not in noise_ids:
        machine_id = dok_to_machine[dok_id]
        machine_parts[machine_id][art_id] += 1

# Show top machines with their most-used real parts
print("Top 10 machines with their most frequently used REAL parts:\n")
sorted_machines = sorted(machine_parts.items(), key=lambda x: sum(x[1].values()), reverse=True)
for machine_id, parts_counter in sorted_machines[:10]:
    prod = produkt_lookup.get(machine_id, {})
    title = prod.get("titel", "?")[:70]
    sn = prod.get("seriennummer", "")
    total_parts_used = sum(parts_counter.values())
    unique_parts = len(parts_counter)
    print(f"  [{machine_id}] {title}")
    if sn:
        print(f"    Serial: {sn}")
    print(f"    {total_parts_used} part usages, {unique_parts} unique parts")
    print(f"    Top parts:")
    for art_id, cnt in parts_counter.most_common(5):
        art = artikel_lookup.get(art_id, {})
        print(f"      {cnt:>3}x  [{art.get('nummer','?')}] {art.get('lang1Titel','?')[:55]}")
    print()


# ── 4. Machine type → Part type patterns ────────────────────────────────

section("4. MACHINE TYPE → PART PATTERNS — Which machine types need which parts?")

# Group machines by type
machine_type_parts = defaultdict(Counter)
for machine_id, parts_counter in machine_parts.items():
    prod = produkt_lookup.get(machine_id, {})
    title = prod.get("titel", "")
    parts = [x.strip() for x in title.split("   ") if x.strip()]
    mtype = parts[0] if parts else "Unknown"
    for art_id, cnt in parts_counter.items():
        machine_type_parts[mtype][art_id] += cnt

print("Parts most specific to each machine type (top 5 types):\n")
for mtype, parts_counter in sorted(machine_type_parts.items(), key=lambda x: sum(x[1].values()), reverse=True)[:8]:
    total = sum(parts_counter.values())
    print(f"  {mtype} ({total} total part usages)")
    for art_id, cnt in parts_counter.most_common(5):
        art = artikel_lookup.get(art_id, {})
        print(f"    {cnt:>3}x  [{art.get('nummer','?')}] {art.get('lang1Titel','?')[:55]}")
    print()


# ── 5. Service document descriptions — what problems are described? ─────

section("5. SERVICE JOB DESCRIPTIONS — What problems get reported?")

service_docs = [d for d in dokumente if d.get("typ") == "s"]
print(f"Service documents (Serviceaufträge): {len(service_docs)}")

# Extract keywords from descriptions
all_words = Counter()
problem_phrases = Counter()
for d in service_docs:
    desc = d.get("bezeichnung", "") + " " + d.get("beschreibung", "")
    desc = re.sub(r'<[^>]+>', '', desc).lower()
    words = re.findall(r'[a-zäöü]{4,}', desc)
    all_words.update(words)

# Filter meaningful keywords
STOP_WORDS = {"dass", "sich", "wird", "oder", "nach", "noch", "auch", "über",
              "alle", "wenn", "eine", "sein", "sind", "wird", "haben", "wurde",
              "nicht", "kann", "dies", "hier", "bitte", "gramag", "gerne",
              "unsere", "ihre", "sehr", "gruss", "freundliche", "geehrte",
              "damen", "herren", "gemäss", "gemä", "herr", "frau",
              "telefon", "mobile", "email", "mail", "info", "reiden",
              "kreuzmatte", "schweiz", "swiss", "zentrale"}

print("\nMost common words in service descriptions:")
for word, c in all_words.most_common(60):
    if word not in STOP_WORDS and c >= 10:
        print(f"  {c:>5}x  {word}")

# Sample service titles
print("\nSample service job titles (last 30):")
for d in service_docs[-30:]:
    title = d.get("bezeichnung", "").strip()
    if title:
        print(f"  - {title[:90]}")


# ── 6. Graph edges summary ──────────────────────────────────────────────

section("6. KNOWLEDGE GRAPH EDGES SUMMARY")

# Count relationships
cust_machine = defaultdict(set)
for p in produkte:
    kid = p.get("ref_kunde", "0")
    if kid and kid != "0":
        cust_machine[kid].add(p["id"])

machine_docs = defaultdict(set)
for d in dokumente:
    pid = d.get("ref_produkt", "0")
    if pid and pid != "0":
        machine_docs[pid].add(d["id"])

print(f"""
Relationship counts for Knowledge Graph:

  Customer → Machine:     {sum(len(v) for v in cust_machine.values()):>8,} edges  ({len(cust_machine)} customers)
  Machine → ServiceJob:   {sum(len(v) for v in machine_docs.values()):>8,} edges  ({len(machine_docs)} machines)
  ServiceJob → Part:      {len(dok_artikel):>8,} edges  (line items)
  Part ↔ Part (co-occur): {len(pair_counts):>8,} edges  (filtered, real parts only)

Node counts:
  Customers:              {len(cust_machine):>8,}
  Machines:               {len(produkte):>8,}
  Parts:                  {len(real_parts):>8,}  (excl. shipping/travel)
  Service Documents:      {len(dokumente):>8,}
  Comments:               ~10,000
  Emails:                 ~16,000
  Supplier PDFs:          ~267 files, 1.7 GB
""")
