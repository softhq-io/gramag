"""Gramag — Bulk CSV → FalkorDB importer (all 115 ERP tables).

Imports every CSV into the graph:
  Phase 1: Create nodes (MERGE on erp_id, skip already-seeded labels)
  Phase 2: Create edges from ref_* columns
  Phase 3: Expand join tables (dok_artikel, dok_produkte, etc.)

Special handling:
  - dokumente.csv typ='s' → ServiceJob (already seeded), others → Dokument
  - benutzer.csv  uses 'uid' not 'id'
  - kommentare.csv  polymorphic ref_typ + ref_id → edges to multiple labels
  - historie.csv   polymorphic ref_typ + ref_id (skip nodes — too large/noisy)
  - Tables without 'id'/'uid' → edge-only (join tables)

All writes use MERGE for idempotent re-runs.
"""

import csv
import os
import re
import time
from config import ERP_DIR
from db import db
from seed_erp import clean_html

csv.field_size_limit(10_000_000)

# ─── Configuration ────────────────────────────────────────────────────

# Tables already seeded by seed_erp.py — skip node creation
ALREADY_SEEDED = {
    "kunden",       # → Customer
    "produkte",     # → Machine
    "artikel",      # → Part
    "adressen",     # → merged into Customer (no own label)
}
# dokumente: typ='s' already seeded as ServiceJob, others get new Dokument label
# kommentare: already seeded as ServiceComment (only ref_typ='dok')

# Curated label overrides (table → label)
LABEL_MAP = {
    "kunden":       "Customer",
    "produkte":     "Machine",
    "artikel":      "Part",
    "adressen":     "Adressen",
    "kontakte":     "Kontakte",
    "benutzer":     "Benutzer",
    "dok_leistungen": "DokLeistungen",
    "aufgaben":     "Aufgaben",
    "emails":       "Emails",
    "lagerbestand": "Lagerbestand",
    "chancen":      "Chancen",
    "vertraege":    "Vertraege",
    "lieferantenrechnungen": "Lieferantenrechnungen",
    "dateien":      "Dateien",
    "historie":     "Historie",
    "buchungen":    "Buchungen",
    "belege":       "Belege",
    "kampagnen":    "Kampagnen",
    "termine":      "Termine",
    "wissen_beitraege": "WissenBeitraege",
    "wissen_kategorien": "WissenKategorien",
}

# Curated edge names: (source_table, ref_column) → edge type
# Falls back to generic REF_<COLNAME> if not listed here
EDGE_NAMES = {
    # Dokumente / ServiceJob edges
    ("dokumente", "ref_produkt"):   "FOR_MACHINE",
    ("dokumente", "ref_kunde"):     "FOR_CUSTOMER",
    ("dokumente", "ref_kontakt"):   "FOR_CONTACT",
    ("dokumente", "ref_user"):      "ASSIGNED_TO",
    ("dokumente", "ref_chance"):    "FOR_CHANCE",
    ("dokumente", "ref_vertrag"):   "FOR_CONTRACT",
    ("dokumente", "ref_dok"):       "FOLLOWS_DOK",
    # Emails
    ("emails", "ref_kunde"):        "EMAIL_TO_CUSTOMER",
    ("emails", "ref_kontakt"):      "EMAIL_TO_CONTACT",
    ("emails", "ref_produkt"):      "EMAIL_ABOUT_MACHINE",
    ("emails", "ref_user"):         "EMAIL_BY",
    ("emails", "ref_dok"):          "EMAIL_FOR_DOK",
    ("emails", "ref_chance"):       "EMAIL_FOR_CHANCE",
    ("emails", "ref_vertrag"):      "EMAIL_FOR_CONTRACT",
    # Kontakte
    ("kontakte", "ref_kunde"):      "CONTACT_OF",
    ("kontakte", "ref_user"):       "MANAGED_BY",
    # Aufgaben
    ("aufgaben", "ref_kunde"):      "TASK_FOR_CUSTOMER",
    ("aufgaben", "ref_kontakt"):    "TASK_FOR_CONTACT",
    ("aufgaben", "ref_produkt"):    "TASK_FOR_MACHINE",
    ("aufgaben", "ref_user"):       "TASK_ASSIGNED_TO",
    ("aufgaben", "ref_dok"):        "TASK_FOR_DOK",
    ("aufgaben", "ref_chance"):     "TASK_FOR_CHANCE",
    ("aufgaben", "ref_vertrag"):    "TASK_FOR_CONTRACT",
    ("aufgaben", "ref_bereich"):    "TASK_IN_BEREICH",
    # DokLeistungen (time entries)
    ("dok_leistungen", "ref_dok"):  "LEISTUNG_FOR_DOK",
    ("dok_leistungen", "ref_user"): "LEISTUNG_BY",
    ("dok_leistungen", "ref_artikel"): "LEISTUNG_ARTIKEL",
    # Vertraege
    ("vertraege", "ref_kunde"):     "CONTRACT_WITH",
    ("vertraege", "ref_produkt"):   "CONTRACT_FOR_MACHINE",
    ("vertraege", "ref_user"):      "CONTRACT_MANAGED_BY",
    # Chancen
    ("chancen", "ref_kunde"):       "CHANCE_FOR_CUSTOMER",
    ("chancen", "ref_kontakt"):     "CHANCE_FOR_CONTACT",
    ("chancen", "ref_produkt"):     "CHANCE_FOR_MACHINE",
    ("chancen", "ref_user"):        "CHANCE_ASSIGNED_TO",
    # Lieferantenrechnungen
    ("lieferantenrechnungen", "ref_kunde"): "INVOICE_FROM_SUPPLIER",
    ("lieferantenrechnungen", "ref_user"):  "INVOICE_MANAGED_BY",
    # dok_team
    ("dok_team", "ref_dok"):        "TEAM_ON_DOK",
    ("dok_team", "ref_user"):       "TEAM_MEMBER",
    # dok_fremdkosten
    ("dok_fremdkosten", "ref_dok"): "FREMDKOSTEN_FOR_DOK",
    ("dok_fremdkosten", "ref_artikel"): "FREMDKOSTEN_ARTIKEL",
    ("dok_fremdkosten", "ref_user"): "FREMDKOSTEN_BY",
    # aufgaben_leistungen
    ("aufgaben_leistungen", "ref_aufgabe"): "LEISTUNG_FOR_AUFGABE",
    ("aufgaben_leistungen", "ref_user"): "AUFG_LEISTUNG_BY",
    ("aufgaben_leistungen", "ref_artikel"): "AUFG_LEISTUNG_ARTIKEL",
    # artikel_setpositionen
    ("artikel_setpositionen", "ref_set"): "PART_OF_SET",
    ("artikel_setpositionen", "ref_artikel"): "SET_CONTAINS",
    # gruppen_zuweis
    ("gruppen_zuweis", "ref_gruppe"): "IN_GROUP",
    ("gruppen_zuweis", "ref_kunde"): "GROUP_CUSTOMER",
    ("gruppen_zuweis", "ref_kontakt"): "GROUP_CONTACT",
    # lieferantenrechnungen_aktivitaeten
    ("lieferantenrechnungen_aktivitaeten", "ref_rechnung"): "AKTIVITAET_RECHNUNG",
    ("lieferantenrechnungen_aktivitaeten", "ref_dok"): "AKTIVITAET_DOK",
    # ek_preise
    ("ek_preise", "ref_kunde"): "PRICE_FROM_SUPPLIER",
}

# ref_* column → target table name (for resolving the target label)
REF_TARGET = {
    "ref_kunde":        "kunden",
    "ref_kontakt":      "kontakte",
    "ref_produkt":      "produkte",
    "ref_user":         "benutzer",
    "ref_zustaendig":   "benutzer",
    "ref_team":         "benutzer",
    "ref_dok":          "dokumente",     # dual-label: ServiceJob or Dokument
    "ref_dok_link":     "dokumente",
    "ref_art":          "artikel",
    "ref_artikel":      "artikel",
    "ref_zubehoer":     "artikel",
    "ref_set":          "artikel",
    "ref_artsetpos":    "artikel",
    "ref_chance":       "chancen",
    "ref_vertrag":      "vertraege",
    "ref_aufgabe":      "aufgaben",
    "ref_bereich":      "bereiche",
    "ref_kategorie":    "kategorien",
    "ref_termin":       "termine",
    "ref_adresse":      "adressen",
    "ref_adresse_eigen": "adressen",
    "ref_kommentar":    "kommentare",
    "ref_wissen":       "wissen_beitraege",
    "ref_kat":          "wissen_kategorien",
    "ref_ordner":       "dateien",
    "ref_file":         "dateien",
    "ref_gruppe":       "gruppen",
    "ref_kampagne":     "kampagnen",
    "ref_paket":        "aufgaben_pakete",
    "ref_rechnung":     "dokumente",     # rechnung is a doc type
    "ref_gutschrift":   "dokumente",
    "ref_leistung":     "dok_leistungen",
    "ref_a_leistung":   "aufgaben_leistungen",
    "ref_lauf":         "zahlungslaeufe",
    "ref_zahlungslauf": "zahlungslaeufe",
    "ref_import":       "dok_besr_importe",
    "ref_stufe":        "mahnungen",
    "ref_vorlage":      "dokumentvorlagen",
    "ref_bank":         "bankdaten",
    "ref_nummernkreis": "nummernkreise",
    "ref_usergruppe":   "usergruppen",
    "ref_status":       "status",
    "ref_beleg":        "belege",
    "ref_zahlungsbeleg": "zahlungsbelege",
    "ref_mwst":         "mwst",
    "ref_zeitplan":     "zeitplaene",
    "ref_abwesenheit":  "zeiterfassung_abwesenheiten",
    "ref_feiertag":     "feiertage",
    "ref_tab":          None,   # UI config, skip
    "ref_optional":     None,   # UI config, skip
    "ref_kde_optional": None,   # UI config, skip
    "ref_produkt_kategorie": "kategorien",
    "ref_multi_dok":    None,   # multi-value, skip
    "ref_multi_vertraege": None,
    "ref_multi_kunden": None,
    "ref_multi_kontakte": None,
    "ref_multi_produkte": None,
    "ref_multi_entscheider": None,
    "ref_multi_mitbewerber_kunden": None,
    "ref_multi_mitbewerber_kontakte": None,
    "ref_mitbewerber_kunde": "kunden",
    "ref_mitbewerber_kontakt": "kontakte",
    "ref_pos":          None,   # position index, not a FK
    "ref_count":        None,   # counter, not a FK
    "ref_fix":          None,   # internal
    "ref_wieder":       None,   # recurring, skip
    "ref_wiederkehrend": None,
    "ref_teilrechnung": None,   # sub-invoice ref
    "ref_extern":       None,   # external ref
    "ref_merge":        None,   # merge ref
    "ref_anpassung":    None,   # adjustment ref
    "ref_lieferung":    "dokumente",
    "ref_schnittstelle": None,  # accounting interface, skip
    "ref_mwst_fixier_daten": None,
    "ref_mwst_bezugssteuer_vorsteuer": None,
    "ref_mwst_bezugssteuer_umsatzsteuer": None,
    "ref_mwst_buchung": None,
    "ref_konto":        None,   # accounting, skip
    "ref_skonto_konto": None,
    "ref_finanzanlage": None,
    "ref_wechselkurs_verlust_konto": None,
    "ref_wechselkurs_gewinn_konto": None,
    "ref_journal_operation": None,
    "ref_journal_buchung": None,
    "ref_fremdwaehrung_operation": None,
    "ref_besr_import":  None,
    "ref_dok_zahlung":  None,
    "ref_lfr":          "lieferantenrechnungen",
    "ref_lfr_zahlung":  None,
    "ref_lfr_zahlungslauf": None,
    "ref_kredi":        "lieferantenrechnungen",
    "ref_dokument":     "dokumente",
    "ref_vorlage_pos":  None,
    "ref_buchung":      None,
    "ref_zahlung_dok":  None,
    "ref_zahlung_lfr":  None,
}

# Polymorphic ref_typ values → (target label, id_prop)
POLY_TYP_MAP = {
    "dok":      "dokumente",
    "eml":      "emails",
    "kunde":    "kunden",
    "kde":      "kunden",
    "produkt":  "produkte",
    "pdk":      "produkte",
    "artikel":  "artikel",
    "art":      "artikel",
    "kontakt":  "kontakte",
    "aufgabe":  "aufgaben",
    "auf":      "aufgaben",
    "chance":   "chancen",
    "cha":      "chancen",
    "kre":      "lieferantenrechnungen",
    "inf":      None,    # info entries, skip
    "ber":      "bereiche",
    "zei":      "zeiterfassung",
    "spe":      "spesen_beleg",
    "wis":      "wissen_beitraege",
    "lei":      "dok_leistungen",
    "kmp":      "kampagnen",
    "abw":      "zeiterfassung_abwesenheiten",
    "tmb":      None,    # thumbnail, skip
    "atd":      None,    # attachment data, skip
    "pdf":      None,    # pdf config, skip
    "vor":      None,    # template, skip
    "kom":      "kommentare",
    "emp":      None,    # receipt, skip
}

# Properties to skip (binary, HTML blobs, internal config)
SKIP_PROPS = {
    "beitrag", "beschreibung_html", "mailSignature", "sdata_config",
    "json", "teilnehmer", "process_info", "mailchimp_sync_data",
}

MAX_PROP_LEN = 500

# ─── Helpers ──────────────────────────────────────────────────────────


def csv_to_label(table_name: str) -> str:
    """dok_leistungen → DokLeistungen"""
    return "".join(word.capitalize() for word in table_name.split("_"))


def label_for(table_name: str) -> str:
    """Get the graph label for a table."""
    return LABEL_MAP.get(table_name, csv_to_label(table_name))


def id_column_for(table_name: str) -> str | None:
    """Return the source CSV column used as erp_id."""
    if table_name == "benutzer":
        return "uid"
    return "id"


def resolve_target(ref_col: str, table_name: str) -> tuple[str, str] | None:
    """Resolve a ref_* column to (target_label, target_id_prop).

    Returns None if the ref should be skipped.
    For ref_dok targets, returns dual-resolution list handled separately.
    """
    target_table = REF_TARGET.get(ref_col)
    if target_table is None:
        return None

    target_label = label_for(target_table)
    target_id_prop = "erp_id"

    # dokumente is split: ServiceJob (typ=s) and Dokument (all others)
    # For ref_dok, ref_rechnung etc. we'll resolve at query time with dual OPTIONAL MATCH
    return (target_label, target_id_prop)


def load_csv(fname: str) -> tuple[list[str], list[dict]]:
    """Load CSV, return (headers, rows)."""
    path = os.path.join(ERP_DIR, fname)
    if not os.path.exists(path):
        return [], []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def safe_key(key: str) -> str:
    """Sanitize a column name for use as Cypher property key."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", key)


def clean_props(row: dict, headers: list[str], id_col: str) -> dict:
    """Extract graph-safe properties from a CSV row."""
    props = {}
    for h in headers:
        if h == id_col or h.startswith("ref_") or h in SKIP_PROPS:
            continue
        val = row.get(h, "")
        if not val or val == "0":
            continue
        if len(val) > MAX_PROP_LEN:
            val = val[:MAX_PROP_LEN]
        sk = safe_key(h)
        # Try numeric conversion
        try:
            if "." in val:
                num = float(val)
                if num != 0:
                    props[sk] = num
                continue
            else:
                num = int(val)
                if num != 0:
                    props[sk] = num
                continue
        except ValueError:
            pass
        props[sk] = val
    return props


def batch_size_for(total: int) -> int:
    """Choose batch size based on row count."""
    if total > 100_000:
        return 500   # small batches for huge tables to avoid OOM
    if total > 20_000:
        return 500
    return 500


def write_node_batch(label: str, batch: list[dict], all_keys: list[str]):
    """Write a batch of nodes using UNWIND + MERGE."""
    set_parts = [f"n.{k} = row.{k}" for k in all_keys if k != "erp_id"]
    set_clause = ", ".join(set_parts) if set_parts else "n._imported = true"

    cypher = f"""
        UNWIND $batch AS row
        MERGE (n:{label} {{erp_id: row.erp_id}})
        SET {set_clause}
    """
    try:
        db.write(cypher, {"batch": batch})
    except Exception as e:
        print(f"    WARN node batch for {label}: {str(e)[:100]}")


def write_edge_batch(
    src_label: str, tgt_label: str, edge_type: str, batch: list[dict]
):
    """Write a batch of edges using UNWIND + MERGE."""
    cypher = f"""
        UNWIND $batch AS row
        MATCH (a:{src_label} {{erp_id: row.src}})
        MATCH (b:{tgt_label} {{erp_id: row.tgt}})
        MERGE (a)-[:{edge_type}]->(b)
    """
    try:
        db.write(cypher, {"batch": batch})
    except Exception as e:
        print(f"    WARN edge {src_label}-[{edge_type}]->{tgt_label}: {str(e)[:100]}")


def write_edge_batch_dual_target(
    src_label: str, tgt_label_a: str, tgt_label_b: str,
    edge_type: str, batch: list[dict]
):
    """Write edges where target could be either of two labels (e.g. ServiceJob or Dokument)."""
    cypher = f"""
        UNWIND $batch AS row
        MATCH (a:{src_label} {{erp_id: row.src}})
        OPTIONAL MATCH (b1:{tgt_label_a} {{erp_id: row.tgt}})
        OPTIONAL MATCH (b2:{tgt_label_b} {{erp_id: row.tgt}})
        WITH a, row, coalesce(b1, b2) AS b
        WHERE b IS NOT NULL
        MERGE (a)-[:{edge_type}]->(b)
    """
    try:
        db.write(cypher, {"batch": batch})
    except Exception as e:
        print(f"    WARN dual-edge {src_label}-[{edge_type}]->{tgt_label_a}|{tgt_label_b}: {str(e)[:100]}")


# ─── Phase 1: Node creation ──────────────────────────────────────────

def phase1_dokument_nodes():
    """Create Dokument nodes for non-service dokumente."""
    print("\n--- Phase 1a: Dokument nodes (non-service) ---")
    headers, rows = load_csv("dokumente.csv")
    if not rows:
        return

    non_service = [r for r in rows if r.get("typ") != "s"]
    print(f"  dokumente.csv: {len(rows)} total, {len(non_service)} non-service → Dokument")

    id_col = "id"
    all_keys_set = set()
    clean_rows = []
    for row in non_service:
        erp_id = row.get(id_col, "")
        if not erp_id:
            continue
        props = clean_props(row, headers, id_col)
        props["erp_id"] = erp_id
        props["typ"] = row.get("typ", "")
        all_keys_set.update(props.keys())
        clean_rows.append(props)

    if not clean_rows:
        return

    all_keys = sorted(all_keys_set)
    # Normalize: every row must have every key
    for r in clean_rows:
        for k in all_keys:
            r.setdefault(k, None)

    bs = batch_size_for(len(clean_rows))
    for i in range(0, len(clean_rows), bs):
        batch = clean_rows[i:i + bs]
        write_node_batch("Dokument", batch, all_keys)
        print(f"    Dokument: {min(i + bs, len(clean_rows))}/{len(clean_rows)}")


def phase1_benutzer_nodes():
    """Create Benutzer nodes from benutzer.csv (uid-based)."""
    print("\n--- Phase 1b: Benutzer nodes ---")
    headers, rows = load_csv("benutzer.csv")
    if not rows:
        return

    clean_rows = []
    all_keys_set = set()
    for row in rows:
        uid = row.get("uid", "")
        if not uid:
            continue
        props = clean_props(row, headers, "uid")
        props["erp_id"] = uid
        all_keys_set.update(props.keys())
        clean_rows.append(props)

    if not clean_rows:
        return

    all_keys = sorted(all_keys_set)
    for r in clean_rows:
        for k in all_keys:
            r.setdefault(k, None)

    write_node_batch("Benutzer", clean_rows, all_keys)
    print(f"  Benutzer: {len(clean_rows)} nodes")


def phase1_kommentar_nodes():
    """Create ServiceComment nodes for non-dok ref_typ kommentare (already seeded for dok)."""
    print("\n--- Phase 1c: ServiceComment nodes (non-dok) ---")
    headers, rows = load_csv("kommentare.csv")
    if not rows:
        return

    # seed_erp.py already created ServiceComment for ref_typ='dok'
    # Create nodes for ALL kommentare (MERGE is idempotent)
    non_dok = [r for r in rows if r.get("ref_typ") != "dok"]
    print(f"  kommentare.csv: {len(rows)} total, {len(non_dok)} non-dok")

    clean_rows = []
    all_keys_set = set()
    for row in non_dok:
        erp_id = row.get("id", "")
        if not erp_id:
            continue
        text = row.get("kommentar", "").strip()
        if text:
            text = clean_html(text)
        if len(text) < 10:
            continue
        props = {
            "erp_id": erp_id,
            "text": text[:1000],
            "date": row.get("datum", ""),
            "ref_typ": row.get("ref_typ", ""),
        }
        all_keys_set.update(props.keys())
        clean_rows.append(props)

    if not clean_rows:
        print("  No new kommentar nodes to create")
        return

    all_keys = sorted(all_keys_set)
    for r in clean_rows:
        for k in all_keys:
            r.setdefault(k, None)

    bs = batch_size_for(len(clean_rows))
    for i in range(0, len(clean_rows), bs):
        batch = clean_rows[i:i + bs]
        write_node_batch("ServiceComment", batch, all_keys)
        print(f"    ServiceComment (non-dok): {min(i + bs, len(clean_rows))}/{len(clean_rows)}")


def phase1_generic_nodes():
    """Create nodes for all remaining CSV tables with an id column."""
    print("\n--- Phase 1d: Generic entity nodes ---")

    # Tables to skip for node creation
    skip_nodes = {
        # Already seeded by seed_erp.py
        "kunden", "produkte", "artikel", "adressen",
        # Handled specially in phase1 above
        "dokumente", "kommentare", "benutzer",
        # Join tables without id column
        "bereiche_status", "dok_produkte", "dok_zusatzadressen",
        "mitarbeiter", "vertraege_zusatzadressen",
        # Join/link tables — should only create edges, not nodes
        "dok_artikel",       # 101K rows — doc→article link (phase 4)
        "dok_fremdkosten",   # doc external costs
        "dok_rabatte",       # doc discount lines
        "dok_rabatte_vorlagen",
        "dok_budget_gutschriften",
        "dok_zahlungen",     # doc payments
        "dok_besr_zahlungen",
        "dok_besr_importe",
        "aufgaben_pakete",
        "aufgaben_pakete_items",
        "artikel_zubehoer",
        "adressen_zuweis",
        "bereiche_users",
        "kampagnen_zuweis",
        "usergruppen_users",
        "vertraege_positionen",
        "vertraege_generiert",
        "vertraege_vorschlaege",
        "vertraege_zahlungslaeufe",
        "lieferantenrechnungen_aktivitaeten",
        "lieferantenrechnungen_zahlungen",
        "mitarbeiter_leistungsarten",
        "zahlungslaeufe_rechnungen",
        "emoji_reaktion_zuweis",
        "dateien_versionen",
        "konten_summen_zuweis",
        "spesen_antrag",
        "fremdwaehrung_operationen_journal_buchungen",
        # Large tables — skip nodes (accounting/stock movements)
        "journal_buchungen", # 40K accounting
        "lagerbuchungen",    # 23K stock movements
        # Large price/financial tables — edges or skip
        "ek_preise",         # 25K purchase prices
        "vk_preise",         # 12K selling prices
        "buchungen",         # 8K bookings
        "belege",            # 11K receipts
        # Accounting/config/system tables — not useful for knowledge assistant
        "bankenstamm",       # 20K Swiss bank registry
        "bankdaten", "bankdaten_kredi",
        "konten", "konten_gruppen", "konten_summen",
        "konto_budgets", "kurse",
        "mwst", "mwst_fixier_daten", "mwst_ziffern",
        "waehrungen", "zahlung", "zahlungsbelege", "zahlungslaeufe",
        "fremdwaehrung_operationen",
        "journal_operationen",
        "mahnlaeufe", "mahnstufen", "mahnungen",
        "nummernkreise", "schnittstellen",
        "countrys", "language",
        "dashboard_configurations",
        "pdf_konfigurationen", "pdf_vorlagen", "dokumentvorlagen",
        "optionale_felder", "produkte_felder", "produkte_kategorien",
        "zeitplaene", "seriennummern",
        "wissen_versionen",
        "unterschriften",
        "zeiterfassung_arbeitszeiten",
        "zeiterfassung_genehmigungen",
        "zeiterfassung_ueberzeit",
        "zeiterfassung_urlaubsguthaben",
    }

    csvs = sorted(f for f in os.listdir(ERP_DIR) if f.endswith(".csv"))
    for fname in csvs:
        table = fname.replace(".csv", "")
        if table in skip_nodes:
            continue

        headers, rows = load_csv(fname)
        if not rows:
            continue

        id_col = id_column_for(table)
        if id_col not in (headers or []):
            continue  # no usable id column

        label = label_for(table)
        all_keys_set = set()
        clean_rows = []

        for row in rows:
            erp_id = row.get(id_col, "")
            if not erp_id:
                continue
            props = clean_props(row, headers, id_col)
            props["erp_id"] = erp_id
            all_keys_set.update(props.keys())
            clean_rows.append(props)

        if not clean_rows:
            continue

        all_keys = sorted(all_keys_set)
        for r in clean_rows:
            for k in all_keys:
                r.setdefault(k, None)

        bs = batch_size_for(len(clean_rows))
        for i in range(0, len(clean_rows), bs):
            batch = clean_rows[i:i + bs]
            write_node_batch(label, batch, all_keys)

        print(f"  {fname:50s} → {label:25s} +{len(clean_rows):>7,} nodes")


# ─── Phase 2: Edge creation ──────────────────────────────────────────

def _is_dok_target(ref_col: str) -> bool:
    """Check if a ref column targets dokumente (which is dual-label)."""
    target_table = REF_TARGET.get(ref_col)
    return target_table == "dokumente"


def phase2_edges():
    """Create edges for all ref_* columns across all CSVs."""
    print("\n--- Phase 2: Edges from ref_* columns ---")

    # Tables without id that are pure join tables — handled in phase 3
    join_tables = {
        "bereiche_status", "dok_produkte", "dok_zusatzadressen",
        "mitarbeiter", "vertraege_zusatzadressen",
    }

    # Tables where we have NO source nodes — skip to save time
    no_source_nodes = {
        "journal_buchungen", "lagerbuchungen",
        "bankenstamm", "ek_preise", "vk_preise", "buchungen", "belege",
        "bankdaten", "bankdaten_kredi", "konten", "konten_gruppen",
        "konten_summen", "konten_summen_zuweis", "konto_budgets", "kurse",
        "mwst", "mwst_fixier_daten", "mwst_ziffern",
        "waehrungen", "zahlung", "zahlungsbelege", "zahlungslaeufe",
        "fremdwaehrung_operationen", "fremdwaehrung_operationen_journal_buchungen",
        "journal_operationen", "mahnlaeufe", "mahnstufen", "mahnungen",
        "nummernkreise", "schnittstellen", "countrys", "language",
        "dashboard_configurations", "pdf_konfigurationen", "pdf_vorlagen",
        "dokumentvorlagen", "optionale_felder", "produkte_felder",
        "produkte_kategorien", "zeitplaene", "seriennummern",
        "wissen_versionen", "unterschriften",
        "zeiterfassung_arbeitszeiten", "zeiterfassung_genehmigungen",
        "zeiterfassung_ueberzeit", "zeiterfassung_urlaubsguthaben",
        # Join tables that were also skipped for nodes
        "dok_artikel",  # handled in phase 4
        "dok_fremdkosten", "dok_rabatte", "dok_rabatte_vorlagen",
        "dok_budget_gutschriften", "dok_zahlungen", "dok_besr_zahlungen",
        "dok_besr_importe", "aufgaben_pakete",
        "aufgaben_pakete_items", "artikel_zubehoer",
        "adressen_zuweis", "bereiche_users",
        "kampagnen_zuweis", "usergruppen_users", "vertraege_positionen",
        "vertraege_generiert", "vertraege_vorschlaege", "vertraege_zahlungslaeufe",
        "lieferantenrechnungen_zahlungen",
        "mitarbeiter_leistungsarten", "zahlungslaeufe_rechnungen",
        "emoji_reaktion_zuweis", "dateien_versionen", "spesen_antrag",
    }

    csvs = sorted(f for f in os.listdir(ERP_DIR) if f.endswith(".csv"))
    total_edges = 0

    for fname in csvs:
        table = fname.replace(".csv", "")

        if table in join_tables:
            continue  # handled in phase 3
        if table in no_source_nodes:
            continue  # no source nodes, edges would be no-ops
        if table == "adressen":
            continue  # merged into Customer via seed_erp.py, no own label

        headers, rows = load_csv(fname)
        if not rows:
            continue

        id_col = id_column_for(table)
        if id_col not in (headers or []):
            continue

        # Determine source label
        if table == "dokumente":
            # dokumente edges handled specially — ServiceJob and Dokument share same CSV
            _edges = _phase2_dokumente_edges(headers, rows)
            total_edges += _edges
            continue

        if table == "kommentare":
            # kommentare edges handled specially (polymorphic ref_typ)
            _edges = _phase2_kommentar_edges(headers, rows)
            total_edges += _edges
            continue

        src_label = label_for(table)

        # Process each ref_* column
        ref_cols = [h for h in headers if h.startswith("ref_") and h != "ref_id" and h != "ref_typ"]
        table_edges = 0

        for ref_col in ref_cols:
            target = resolve_target(ref_col, table)
            if target is None:
                continue

            tgt_label, tgt_id_prop = target
            edge_type = EDGE_NAMES.get((table, ref_col), ref_col.upper())
            is_dual = _is_dok_target(ref_col)

            batch = []
            for row in rows:
                src_id = row.get(id_col, "")
                tgt_id = row.get(ref_col, "")
                if not src_id or not tgt_id or tgt_id == "0":
                    continue
                batch.append({"src": src_id, "tgt": tgt_id})

            if not batch:
                continue

            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                chunk = batch[i:i + bs]
                if is_dual:
                    write_edge_batch_dual_target(
                        src_label, "ServiceJob", "Dokument", edge_type, chunk
                    )
                else:
                    write_edge_batch(src_label, tgt_label, edge_type, chunk)

            table_edges += len(batch)

        # Handle polymorphic ref_id + ref_typ (for tables other than kommentare)
        if "ref_id" in headers and "ref_typ" in headers and table != "kommentare":
            poly_edges = _phase2_polymorphic_edges(table, src_label, id_col, rows)
            table_edges += poly_edges

        if table_edges > 0:
            total_edges += table_edges
            print(f"  {fname:50s}   +{table_edges:>7,} edges")

    print(f"\n  Total Phase 2 edges: {total_edges:,}")
    return total_edges


def _phase2_dokumente_edges(headers: list[str], rows: list[dict]) -> int:
    """Create edges from dokumente.csv (dual-label: ServiceJob for typ=s, Dokument otherwise)."""
    ref_cols = [h for h in headers if h.startswith("ref_") and h != "ref_id" and h != "ref_typ"]
    total = 0

    for ref_col in ref_cols:
        target = resolve_target(ref_col, "dokumente")
        if target is None:
            continue

        tgt_label, tgt_id_prop = target
        edge_type = EDGE_NAMES.get(("dokumente", ref_col), ref_col.upper())
        is_dual = _is_dok_target(ref_col)

        # Split by source type
        sj_batch = []  # ServiceJob sources
        dk_batch = []  # Dokument sources
        for row in rows:
            src_id = row.get("id", "")
            tgt_id = row.get(ref_col, "")
            if not src_id or not tgt_id or tgt_id == "0":
                continue
            if row.get("typ") == "s":
                sj_batch.append({"src": src_id, "tgt": tgt_id})
            else:
                dk_batch.append({"src": src_id, "tgt": tgt_id})

        for src_label, batch in [("ServiceJob", sj_batch), ("Dokument", dk_batch)]:
            if not batch:
                continue
            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                chunk = batch[i:i + bs]
                if is_dual:
                    write_edge_batch_dual_target(
                        src_label, "ServiceJob", "Dokument", edge_type, chunk
                    )
                else:
                    write_edge_batch(src_label, tgt_label, edge_type, chunk)
            total += len(batch)

    if total > 0:
        print(f"  {'dokumente.csv':50s}   +{total:>7,} edges (ServiceJob + Dokument)")
    return total


def _phase2_kommentar_edges(headers: list[str], rows: list[dict]) -> int:
    """Create edges from kommentare.csv — polymorphic ref_typ + ref_id."""
    total = 0

    # ref_user edges for all kommentare
    user_batch = []
    for row in rows:
        src_id = row.get("id", "")
        user_id = row.get("ref_user", "")
        if src_id and user_id and user_id != "0":
            user_batch.append({"src": src_id, "tgt": user_id})

    if user_batch:
        bs = batch_size_for(len(user_batch))
        for i in range(0, len(user_batch), bs):
            write_edge_batch("ServiceComment", "Benutzer", "COMMENT_BY", user_batch[i:i + bs])
        total += len(user_batch)

    # Polymorphic ref_typ + ref_id
    by_typ: dict[str, list[dict]] = {}
    for row in rows:
        src_id = row.get("id", "")
        ref_id = row.get("ref_id", "")
        ref_typ = row.get("ref_typ", "")
        if not src_id or not ref_id or ref_id == "0" or not ref_typ:
            continue
        by_typ.setdefault(ref_typ, []).append({"src": src_id, "tgt": ref_id})

    for typ, batch in sorted(by_typ.items()):
        target_table = POLY_TYP_MAP.get(typ)
        if target_table is None:
            continue

        tgt_label = label_for(target_table)
        edge_name = f"COMMENT_ON_{typ.upper()}"
        # Special names for common types
        if typ == "dok":
            edge_name = "ON_JOB"  # keep backward compat with seed_erp.py
            # Dual target: ServiceJob or Dokument
            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                write_edge_batch_dual_target(
                    "ServiceComment", "ServiceJob", "Dokument", edge_name, batch[i:i + bs]
                )
        elif typ == "auf":
            edge_name = "COMMENT_ON_AUFGABE"
            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                write_edge_batch("ServiceComment", tgt_label, edge_name, batch[i:i + bs])
        elif typ == "kre":
            edge_name = "COMMENT_ON_INVOICE"
            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                write_edge_batch("ServiceComment", tgt_label, edge_name, batch[i:i + bs])
        elif typ == "cha":
            edge_name = "COMMENT_ON_CHANCE"
            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                write_edge_batch("ServiceComment", tgt_label, edge_name, batch[i:i + bs])
        else:
            bs = batch_size_for(len(batch))
            for i in range(0, len(batch), bs):
                write_edge_batch("ServiceComment", tgt_label, edge_name, batch[i:i + bs])

        total += len(batch)

    if total > 0:
        print(f"  {'kommentare.csv':50s}   +{total:>7,} edges (all ref_typ)")
    return total


def _phase2_polymorphic_edges(
    table: str, src_label: str, id_col: str, rows: list[dict]
) -> int:
    """Handle polymorphic ref_id + ref_typ for tables like historie, dateien, termine."""
    by_typ: dict[str, list[dict]] = {}
    for row in rows:
        src_id = row.get(id_col, "")
        ref_id = row.get("ref_id", "")
        ref_typ = row.get("ref_typ", "")
        if not src_id or not ref_id or ref_id == "0" or not ref_typ:
            continue
        by_typ.setdefault(ref_typ, []).append({"src": src_id, "tgt": ref_id})

    total = 0
    for typ, batch in sorted(by_typ.items()):
        target_table = POLY_TYP_MAP.get(typ)
        if target_table is None:
            continue

        tgt_label = label_for(target_table)
        edge_type = f"REF_{typ.upper()}"
        is_dual = (target_table == "dokumente")

        bs = batch_size_for(len(batch))
        for i in range(0, len(batch), bs):
            chunk = batch[i:i + bs]
            if is_dual:
                write_edge_batch_dual_target(
                    src_label, "ServiceJob", "Dokument", edge_type, chunk
                )
            else:
                write_edge_batch(src_label, tgt_label, edge_type, chunk)
        total += len(batch)

    return total


# ─── Phase 3: Join tables (no id column) ─────────────────────────────

def phase3_join_tables():
    """Create edges for tables that have no id column (pure join tables)."""
    print("\n--- Phase 3: Join tables ---")
    total = 0

    # dok_produkte: ref_dok → ref_produkt
    total += _join_dok_produkte()

    # bereiche_status: ref_bereich → ref_status
    total += _join_simple("bereiche_status.csv", "ref_bereich", "Bereiche",
                          "ref_status", "Status", "HAS_STATUS", dual_src=False)

    # dok_zusatzadressen: ref_dok → ref_kunde, ref_kontakt, ref_adresse
    total += _join_dok_zusatzadressen()

    # mitarbeiter: ref_user → various
    total += _join_mitarbeiter()

    # vertraege_zusatzadressen: ref_vertrag → ref_kunde, ref_kontakt
    total += _join_vertraege_zusatzadressen()

    print(f"\n  Total Phase 3 edges: {total:,}")
    return total


def _join_dok_produkte() -> int:
    """dok_produkte.csv: link Dokument/ServiceJob → Machine."""
    headers, rows = load_csv("dok_produkte.csv")
    if not rows:
        return 0

    batch = []
    for row in rows:
        dok_id = row.get("ref_dok", "")
        prod_id = row.get("ref_produkt", "")
        if dok_id and prod_id and dok_id != "0" and prod_id != "0":
            batch.append({"src": dok_id, "tgt": prod_id})

    if not batch:
        return 0

    bs = batch_size_for(len(batch))
    for i in range(0, len(batch), bs):
        chunk = batch[i:i + bs]
        write_edge_batch_dual_target(
            "ServiceJob", "Machine", "Machine", "DOK_FOR_MACHINE", chunk
        )
        # Also try Dokument source
        write_edge_batch("Dokument", "Machine", "DOK_FOR_MACHINE", chunk)

    print(f"  dok_produkte.csv: +{len(batch)} edges")
    return len(batch)


def _join_simple(
    fname: str, src_col: str, src_label: str,
    tgt_col: str, tgt_label: str, edge_type: str,
    dual_src: bool = False
) -> int:
    headers, rows = load_csv(fname)
    if not rows:
        return 0

    batch = []
    for row in rows:
        src = row.get(src_col, "")
        tgt = row.get(tgt_col, "")
        if src and tgt and src != "0" and tgt != "0":
            batch.append({"src": src, "tgt": tgt})

    if not batch:
        return 0

    bs = batch_size_for(len(batch))
    for i in range(0, len(batch), bs):
        write_edge_batch(src_label, tgt_label, edge_type, batch[i:i + bs])

    print(f"  {fname}: +{len(batch)} edges")
    return len(batch)


def _join_dok_zusatzadressen() -> int:
    """dok_zusatzadressen: link docs to additional customers/contacts."""
    headers, rows = load_csv("dok_zusatzadressen.csv")
    if not rows:
        return 0

    total = 0
    # ref_dok → ref_kunde
    batch_kunde = []
    batch_kontakt = []
    for row in rows:
        dok_id = row.get("ref_dok", "")
        if not dok_id or dok_id == "0":
            continue
        kunde_id = row.get("ref_kunde", "")
        if kunde_id and kunde_id != "0":
            batch_kunde.append({"src": dok_id, "tgt": kunde_id})
        kontakt_id = row.get("ref_kontakt", "")
        if kontakt_id and kontakt_id != "0":
            batch_kontakt.append({"src": dok_id, "tgt": kontakt_id})

    for batch, tgt_label, edge in [
        (batch_kunde, "Customer", "ZUSATZ_KUNDE"),
        (batch_kontakt, "Kontakte", "ZUSATZ_KONTAKT"),
    ]:
        if not batch:
            continue
        bs = batch_size_for(len(batch))
        for i in range(0, len(batch), bs):
            chunk = batch[i:i + bs]
            # Try both ServiceJob and Dokument as source
            write_edge_batch_dual_target(
                "ServiceJob", tgt_label, tgt_label, edge, chunk
            )
            write_edge_batch("Dokument", tgt_label, edge, chunk)
        total += len(batch)

    if total:
        print(f"  dok_zusatzadressen.csv: +{total} edges")
    return total


def _join_mitarbeiter() -> int:
    """mitarbeiter: ref_user → employee info (link to Benutzer)."""
    headers, rows = load_csv("mitarbeiter.csv")
    if not rows:
        return 0

    # No id column — just link ref_user to itself basically
    # The useful info is funktion, vorgesetzter etc. — skip for now, just note it
    print(f"  mitarbeiter.csv: {len(rows)} rows (info-only, no edges)")
    return 0


def _join_vertraege_zusatzadressen() -> int:
    headers, rows = load_csv("vertraege_zusatzadressen.csv")
    if not rows:
        return 0

    total = 0
    batch_kunde = []
    for row in rows:
        vertrag_id = row.get("ref_vertrag", "")
        kunde_id = row.get("ref_kunde", "")
        if vertrag_id and kunde_id and vertrag_id != "0" and kunde_id != "0":
            batch_kunde.append({"src": vertrag_id, "tgt": kunde_id})

    if batch_kunde:
        write_edge_batch("Vertraege", "Customer", "VERTRAG_ZUSATZ_KUNDE", batch_kunde)
        total += len(batch_kunde)

    if total:
        print(f"  vertraege_zusatzadressen.csv: +{total} edges")
    return total


# ─── Phase 4: Expanded dok_artikel (all doc types) ───────────────────

def phase4_dok_artikel():
    """Expand dok_artikel to link ALL doc types (not just ServiceJob) to Parts."""
    print("\n--- Phase 4: dok_artikel (all doc types → Part) ---")
    headers, rows = load_csv("dok_artikel.csv")
    if not rows:
        return 0

    # Build batch: ref_dok → ref_art
    batch = []
    for row in rows:
        dok_id = row.get("ref_dok", "")
        art_id = row.get("ref_art", "")
        if not dok_id or not art_id or dok_id == "0" or art_id == "0":
            continue
        quantity = row.get("anzahl", "0")
        price = row.get("preis", "0")
        batch.append({
            "dok_id": dok_id,
            "art_id": art_id,
            "quantity": quantity,
            "price": price,
        })

    if not batch:
        return 0

    # Use dual-target for dok source (ServiceJob or Dokument)
    cypher = """
        UNWIND $batch AS row
        OPTIONAL MATCH (sj:ServiceJob {erp_id: row.dok_id})
        OPTIONAL MATCH (dk:Dokument {erp_id: row.dok_id})
        WITH row, coalesce(sj, dk) AS doc
        WHERE doc IS NOT NULL
        MATCH (p:Part {erp_id: row.art_id})
        MERGE (doc)-[u:USED_PART]->(p)
        SET u.quantity = toFloat(row.quantity), u.price = toFloat(row.price)
    """

    bs = batch_size_for(len(batch))
    for i in range(0, len(batch), bs):
        chunk = batch[i:i + bs]
        try:
            db.write(cypher, {"batch": chunk})
        except Exception as e:
            print(f"    WARN dok_artikel batch: {str(e)[:100]}")
        done = min(i + bs, len(batch))
        if done % 10000 < bs:
            print(f"    dok_artikel: {done:,}/{len(batch):,}")

    print(f"  dok_artikel.csv: {len(batch):,} line items processed")
    return len(batch)


# ─── Main ─────────────────────────────────────────────────────────────

def verify():
    """Print final graph stats."""
    print("\n" + "=" * 60)
    print("  Verification")
    print("=" * 60)
    stats = db.stats()

    print("\n  Node labels:")
    total_nodes = 0
    for label, count in sorted(stats["nodes"].items()):
        print(f"    {label:30s} {count:>8,}")
        total_nodes += count

    print(f"\n  Total nodes: {total_nodes:,}")

    print("\n  Relationship types:")
    total_rels = 0
    for rel, count in sorted(stats["relationships"].items()):
        print(f"    {rel:30s} {count:>8,}")
        total_rels += count

    print(f"\n  Total relationships: {total_rels:,}")
    print(f"  Labels: {len(stats['nodes'])}")
    print(f"  Rel types: {len(stats['relationships'])}")


def main():
    print("=" * 60)
    print("  Gramag — Bulk CSV Import (all 115 tables)")
    print("=" * 60)

    t0 = time.time()
    db.connect()
    print(f"Connected to FalkorDB ({db.host}:{db.port}/{db.graph_name})")

    # Disable stop-writes-on-bgsave-error to prevent MISCONF failures
    import redis
    r = redis.Redis(host=db.host, port=db.port)
    r.config_set("stop-writes-on-bgsave-error", "no")
    print("  (set stop-writes-on-bgsave-error = no)")

    # Apply schema indexes first
    from schema import apply_indexes
    print("\n--- Applying indexes ---")
    apply_indexes()

    # Phase 1: Create all nodes
    phase1_dokument_nodes()
    phase1_benutzer_nodes()
    phase1_kommentar_nodes()
    phase1_generic_nodes()

    # Phase 2: Create all edges from ref_* columns
    phase2_edges()

    # Phase 3: Join tables (no id column)
    phase3_join_tables()

    # Phase 4: Expanded dok_artikel
    phase4_dok_artikel()

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  Import completed in {elapsed:.1f}s")
    print(f"{'=' * 60}")

    verify()


if __name__ == "__main__":
    main()
