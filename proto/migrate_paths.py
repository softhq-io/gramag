"""Rewrite absolute paths in the proto graph to portable relative form.

After running, Document.path / ImageAsset.path become relative to PROTO_ROOT,
and ManualSection.png_path becomes relative to PROTO_CACHE_DIR. The runtime
resolver (proto.resolve_source / proto.resolve_cache) re-joins the current root
at query time, so the same graph works across environments.

Idempotent: running twice does nothing on already-relative paths.
"""

import os

from proto import PROTO_CACHE_DIR, PROTO_ROOT
from proto.db_proto import proto_db


def _relativize(p: str, root: str) -> str | None:
    if not p:
        return None
    if not os.path.isabs(p):
        return None  # already relative
    root = root.rstrip("/")
    if p.startswith(root + "/"):
        return p[len(root) + 1:]
    # Also match the legacy hardcoded root if currently the same
    return None


def migrate():
    # Documents & ImageAssets share PROTO_ROOT
    result = proto_db.query(
        "MATCH (d:Document) RETURN d.id AS id, d.path AS path"
    )
    count_doc = 0
    for row in result.result_set or []:
        doc_id, path = row[0], row[1]
        rel = _relativize(path, PROTO_ROOT)
        if rel is None:
            continue
        proto_db.write(
            "MATCH (d:Document {id: $id}) SET d.path = $p",
            {"id": doc_id, "p": rel},
        )
        count_doc += 1
    print(f"Document.path rewritten: {count_doc}")

    result = proto_db.query(
        "MATCH (i:ImageAsset) RETURN i.id AS id, i.path AS path"
    )
    count_img = 0
    for row in result.result_set or []:
        img_id, path = row[0], row[1]
        rel = _relativize(path, PROTO_ROOT)
        if rel is None:
            continue
        proto_db.write(
            "MATCH (i:ImageAsset {id: $id}) SET i.path = $p",
            {"id": img_id, "p": rel},
        )
        count_img += 1
    print(f"ImageAsset.path rewritten: {count_img}")

    # ManualSection.png_path → relative to PROTO_CACHE_DIR
    result = proto_db.query(
        "MATCH (s:ManualSection) RETURN s.id AS id, s.png_path AS png"
    )
    count_sec = 0
    for row in result.result_set or []:
        sec_id, png = row[0], row[1]
        rel = _relativize(png, PROTO_CACHE_DIR)
        if rel is None:
            continue
        proto_db.write(
            "MATCH (s:ManualSection {id: $id}) SET s.png_path = $p",
            {"id": sec_id, "p": rel},
        )
        count_sec += 1
    print(f"ManualSection.png_path rewritten: {count_sec}")


if __name__ == "__main__":
    migrate()
