"""Re-summarize all ConfigFile nodes with the improved prompt and refresh embeddings."""

from concurrent.futures import ThreadPoolExecutor, as_completed

from db_helpers import result_to_dicts
from embeddings import generate_embedding
from proto.db_proto import proto_db
from proto.vision import summarize_config, with_retry


def find_all() -> list[dict]:
    r = proto_db.query(
        "MATCH (c:ConfigFile) RETURN c.id AS id, c.name AS name, c.content AS content"
    )
    return result_to_dicts(r)


def reprocess_one(row: dict) -> tuple[str, bool, str]:
    try:
        summary = with_retry(summarize_config, row["name"], row.get("content") or "")
    except Exception as e:
        return row["name"], False, str(e)
    merged = f"FILE: {row['name']}\n\nSUMMARY:\n{summary}\n\nCONTENT:\n{(row.get('content') or '')[:4000]}"
    emb = generate_embedding(merged)
    proto_db.write(
        "MATCH (c:ConfigFile {id: $id}) SET c.summary = $sum, c.embedding = vecf32($emb)",
        {"id": row["id"], "sum": summary, "emb": emb},
    )
    return row["name"], True, f"len={len(summary)}"


def main():
    rows = find_all()
    print(f"Re-summarizing {len(rows)} ConfigFile nodes...")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(reprocess_one, r): r for r in rows}
        for i, f in enumerate(as_completed(futures), 1):
            name, success, info = f.result()
            if success:
                ok += 1
            else:
                fail += 1
            if i % 10 == 0 or not success:
                print(f"  [{i}/{len(rows)}] {'OK' if success else 'FAIL'} {name}")
    print(f"\nDone. OK={ok} FAIL={fail}")


if __name__ == "__main__":
    main()
