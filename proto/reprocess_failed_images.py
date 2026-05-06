"""Re-run vision on ImageAsset nodes that have empty captions."""

from concurrent.futures import ThreadPoolExecutor, as_completed

from db_helpers import result_to_dicts
from embeddings import generate_embedding
from proto.db_proto import proto_db
from proto.vision import vision_caption_image, with_retry


def find_empty() -> list[dict]:
    r = proto_db.query(
        """
        MATCH (i:ImageAsset)
        WHERE i.caption = '' OR size(i.caption) < 20
        RETURN i.id AS id, i.name AS name, i.path AS path,
               i.category AS category, i.rel_path AS rel_path
        """
    )
    return result_to_dicts(r)


def reprocess_one(row: dict) -> tuple[str, bool, str]:
    try:
        caption = with_retry(vision_caption_image, row["path"])
    except Exception as e:
        return row["name"], False, str(e)
    merged = f"IMAGE: {row['name']} (category: {row.get('category')})\n\n{caption}"
    emb = generate_embedding(merged)
    proto_db.write(
        "MATCH (i:ImageAsset {id: $id}) SET i.caption = $cap, i.embedding = vecf32($emb)",
        {"id": row["id"], "cap": caption, "emb": emb},
    )
    return row["name"], True, f"caption_len={len(caption)}"


def main():
    rows = find_empty()
    print(f"Reprocessing {len(rows)} empty-caption images...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(reprocess_one, r): r for r in rows}
        for f in as_completed(futures):
            name, ok, info = f.result()
            print(f"  {'OK' if ok else 'FAIL'}  {name}  {info}")


if __name__ == "__main__":
    main()
