"""Refresh FalkorDB vector embeddings with Azure OpenAI embeddings.

This keeps existing graph nodes and vision/text extraction intact while
replacing old provider vectors.
"""

from __future__ import annotations

import argparse
import time

from db import db
from db_helpers import result_to_dicts
from embeddings import generate_embeddings_batch
from proto.db_proto import proto_db


def _merged(*values: str | None) -> str:
    return "\n\n".join(v for v in values if v and v.strip())


def _refresh_main_manual_sections(batch_size: int) -> int:
    db.connect()
    rows = result_to_dicts(db.query(
        """
        MATCH (n:ManualSection)
        RETURN n.id AS id, n.text AS text, n.summary AS summary, n.title AS title
        """
    ))
    updated = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = [_merged(r.get("title"), r.get("summary"), r.get("text")) for r in batch]
        embeddings = generate_embeddings_batch(texts, batch_size=batch_size, delay=0)
        for row, emb in zip(batch, embeddings):
            if not row.get("id") or not emb:
                continue
            db.write(
                "MATCH (n:ManualSection {id: $id}) SET n.embedding = vecf32($emb)",
                {"id": row["id"], "emb": emb},
            )
            updated += 1
        print(f"main ManualSection: {min(i + batch_size, len(rows))}/{len(rows)}")
    return updated


def _refresh_proto_label(label: str, batch_size: int) -> int:
    proto_db.connect()
    rows = result_to_dicts(proto_db.query(
        f"""
        MATCH (n:{label})
        RETURN n.id AS id, n.text AS text, n.vision_desc AS vision_desc,
               n.merged AS merged, n.summary AS summary, n.content AS content,
               n.caption AS caption, n.name AS name
        """
    ))
    updated = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = [
            _merged(
                row.get("merged"),
                row.get("summary"),
                row.get("caption"),
                row.get("name"),
                row.get("text"),
                row.get("vision_desc"),
                row.get("content"),
            )
            for row in batch
        ]
        embeddings = generate_embeddings_batch(texts, batch_size=batch_size, delay=0)
        for row, emb in zip(batch, embeddings):
            if not row.get("id") or not emb:
                continue
            proto_db.write(
                f"MATCH (n:{label} {{id: $id}}) SET n.embedding = vecf32($emb)",
                {"id": row["id"], "emb": emb},
            )
            updated += 1
        print(f"proto {label}: {min(i + batch_size, len(rows))}/{len(rows)}")
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--scope",
        choices=["all", "main", "proto"],
        default="all",
        help="Which graph embeddings to refresh.",
    )
    args = parser.parse_args()

    t0 = time.time()
    counts = {}
    if args.scope in ("all", "main"):
        counts["main_manual_sections"] = _refresh_main_manual_sections(args.batch_size)
    if args.scope in ("all", "proto"):
        for label in ("ManualSection", "ConfigFile", "ImageAsset"):
            counts[f"proto_{label}"] = _refresh_proto_label(label, args.batch_size)

    print(f"Done in {time.time() - t0:.0f}s: {counts}")


if __name__ == "__main__":
    main()
