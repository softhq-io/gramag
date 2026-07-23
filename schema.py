"""Gramag Knowledge Graph — Schema & index creation."""

from db import db, VECTOR_DIMENSIONS


def _safe_query(cypher: str, label: str):
    """Execute a schema query, ignoring 'already exists' errors."""
    try:
        db.query(cypher)
        print(f"  + {label}")
    except Exception as e:
        err = str(e).lower()
        if "already indexed" in err or "already exists" in err:
            print(f"  = {label} (exists)")
        else:
            print(f"  x {label}: {e}")


def apply_indexes():
    """Create all indexes for the Gramag knowledge graph."""
    db.connect()

    # Range indexes for fast lookups
    print("Range indexes...")
    range_indexes = [
        ("Customer", "erp_id"),
        ("Customer", "name"),
        ("Machine", "erp_id"),
        ("Machine", "serial_number"),
        ("MachineType", "name"),
        ("MachineBrand", "name"),
        ("Part", "erp_id"),
        ("Part", "nummer"),
        ("Part", "manufacturer_nr"),
        ("Supplier", "name"),
        ("ServiceJob", "erp_id"),
        ("ServiceJob", "nummer"),
        ("ServiceComment", "erp_id"),
        ("ManualSection", "supplier"),
        ("ErrorCode", "code"),
        ("TroubleshootingEntry", "symptom"),
        ("Session", "id"),
        ("User", "username"),
        ("User", "username_normalized"),
        ("User", "login_normalized"),
        ("User", "id"),
        ("User", "email_normalized"),
        ("UserAuditEvent", "id"),
        ("UserAuditEvent", "created_at"),
        ("SyncState", "name"),
        ("SyncRun", "id"),
        ("SyncRun", "source"),
        ("SyncRun", "status"),
        # New labels from bulk CSV import
        ("Dokument", "erp_id"),
        ("Dokument", "typ"),
        ("Benutzer", "erp_id"),
        ("Kontakte", "erp_id"),
        ("Emails", "erp_id"),
        ("Aufgaben", "erp_id"),
        ("DokLeistungen", "erp_id"),
        ("Lagerbestand", "erp_id"),
        ("Chancen", "erp_id"),
        ("Vertraege", "erp_id"),
        ("Lieferantenrechnungen", "erp_id"),
        ("Dateien", "erp_id"),
        ("Historie", "erp_id"),
        ("Buchungen", "erp_id"),
        ("Belege", "erp_id"),
        ("Termine", "erp_id"),
        ("Kampagnen", "erp_id"),
        ("WissenBeitraege", "erp_id"),
        ("WissenKategorien", "erp_id"),
    ]
    for label, prop in range_indexes:
        _safe_query(
            f"CREATE INDEX FOR (n:{label}) ON (n.{prop})",
            f"{label}.{prop}",
        )

    # Fulltext indexes for search
    print("\nFulltext indexes...")
    fulltext_indexes = [
        ("Part", ["titel", "nummer", "manufacturer_nr"]),
        ("Machine", ["title"]),
        ("ServiceJob", ["title", "description"]),
        ("ManualSection", ["title", "summary"]),
        ("ErrorCode", ["code", "description", "solution"]),
        ("Customer", ["name"]),
    ]
    for label, props in fulltext_indexes:
        props_str = ", ".join(f"'{p}'" for p in props)
        _safe_query(
            f"CALL db.idx.fulltext.createNodeIndex('{label}', {props_str})",
            f"{label}[{', '.join(props)}]",
        )

    # Vector index for ManualSection embeddings
    print("\nVector indexes...")
    _safe_query(
        f"CREATE VECTOR INDEX FOR (n:ManualSection) ON (n.embedding) "
        f"OPTIONS {{dimension: {VECTOR_DIMENSIONS}, similarityFunction: 'cosine'}}",
        f"ManualSection.embedding (dim={VECTOR_DIMENSIONS})",
    )

    print("\nDone!")


if __name__ == "__main__":
    apply_indexes()
