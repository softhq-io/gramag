"""Schema & indexes for the gramag_proto multimodal KB.

Run: python -m proto.schema
"""

from config import EMBED_DIMENSIONS
from proto.db_proto import proto_db


def _safe(cypher: str, label: str):
    try:
        proto_db.query(cypher)
        print(f"  + {label}")
    except Exception as e:
        err = str(e).lower()
        if "already indexed" in err or "already exists" in err:
            print(f"  = {label} (exists)")
        else:
            print(f"  x {label}: {e}")


def apply_indexes():
    proto_db.connect()

    print("Range indexes...")
    range_indexes = [
        ("Customer", "id"),
        ("Customer", "name"),
        ("Machine", "slug"),
        ("Machine", "customer"),
        ("Machine", "type"),
        ("Machine", "model"),
        ("Machine", "serial"),
        ("Machine", "erp_id"),
        ("Machine", "erp_customer_id"),
        ("Machine", "erp_link_mode"),
        ("Machine", "erp_group_identifier"),
        ("Machine", "erp_match_confidence"),
        ("Document", "id"),
        ("Document", "kind"),
        ("Document", "category"),
        ("DocumentCategory", "name"),
        ("ManualSection", "id"),
        ("ManualSection", "document_id"),
        ("ManualSection", "page"),
        ("ConfigFile", "id"),
        ("ConfigFile", "name"),
        ("ImageAsset", "id"),
        ("ImageAsset", "category"),
        ("ProtoChatSession", "id"),
        ("ProtoChatSession", "machine_slug"),
        ("ProtoChatSession", "customer"),
        ("ProtoChatSession", "client_id"),
        ("ProtoChatSession", "updated_at"),
        ("ProtoChatSession", "created_by"),
        ("ProtoChatSession", "created_by_id"),
        ("ProtoChatSession", "isolation_version"),
        ("ProtoChatMessage", "id"),
        ("ProtoChatMessage", "session_id"),
        ("ProtoChatMessage", "created_at"),
        ("ProtoChatMessage", "username"),
    ]
    for label, prop in range_indexes:
        _safe(
            f"CREATE INDEX FOR (n:{label}) ON (n.{prop})",
            f"{label}.{prop}",
        )

    print("\nFulltext indexes...")
    fulltext = [
        ("Customer", ["name"]),
        ("Machine", ["folder", "type", "model", "serial"]),
        ("Document", ["name", "rel_path"]),
        ("ManualSection", ["text", "vision_desc", "merged"]),
        ("ConfigFile", ["content", "summary"]),
        ("ImageAsset", ["caption", "ocr_text"]),
        ("ProtoChatMessage", ["text"]),
    ]
    for label, props in fulltext:
        props_str = ", ".join(f"'{p}'" for p in props)
        _safe(
            f"CALL db.idx.fulltext.createNodeIndex('{label}', {props_str})",
            f"{label}[{', '.join(props)}]",
        )

    print("\nVector indexes...")
    for label in ("ManualSection", "ConfigFile", "ImageAsset", "ProtoChatMessage"):
        _safe(
            f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
            f"OPTIONS {{dimension: {EMBED_DIMENSIONS}, similarityFunction: 'cosine'}}",
            f"{label}.embedding (dim={EMBED_DIMENSIONS})",
        )

    print("\nDone.")


if __name__ == "__main__":
    apply_indexes()
