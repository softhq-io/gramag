"""Proto retriever — hybrid vector over ManualSection + ConfigFile + ImageAsset.

Optional machine scope filter; cross-machine mode when machine_slug=None.
"""

import re

from db_helpers import result_to_dicts
from embeddings import generate_query_embedding
from proto.db_proto import proto_db

PAGE_RE = re.compile(
    r"\b(?:strona|stronie|strony|strone|page|pages|seite|seiten|s\.|p\.|pg\.?)\s*"
    r"(\d{1,4})(?:\s*(?:i|and|und|,|-)\s*(\d{1,4}))?",
    re.IGNORECASE,
)


def extract_page_refs(query: str) -> list[int]:
    pages = []
    for m in PAGE_RE.finditer(query):
        for g in m.groups():
            if g:
                pages.append(int(g))
    return list(dict.fromkeys(pages))


def _vector_search(label: str, query_embedding: list[float], top_k: int,
                   machine_slug: str | None) -> list[dict]:
    try:
        result = proto_db.query(
            f"""
            CALL db.idx.vector.queryNodes(
                '{label}', 'embedding', $k, vecf32($emb)
            ) YIELD node, score
            OPTIONAL MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document)
            WHERE ((node:ManualSection AND d.id = node.document_id)
                OR (node:ConfigFile AND (d)-[:HAS_CONFIG]->(node))
                OR (node:ImageAsset AND (d)-[:HAS_IMAGE]->(node)))
            WITH node, score, m, d
            WHERE $slug IS NULL OR m.slug = $slug
            RETURN node, score, m.slug AS machine_slug, m.folder AS machine_folder,
                   d.name AS doc_name, d.kind AS doc_kind, d.category AS category,
                   d.id AS document_id
            ORDER BY score DESC
            LIMIT $k
            """,
            {"emb": query_embedding, "k": top_k, "slug": machine_slug},
        )
    except Exception as e:
        print(f"  vector search error on {label}: {e}")
        return []

    out = []
    for row in result.result_set or []:
        node = row[0]
        props = getattr(node, "properties", {}) or {}
        out.append({
            "label": label,
            "id": props.get("id"),
            "score": float(row[1]),
            "machine_slug": row[2],
            "machine_folder": row[3],
            "doc_name": row[4],
            "doc_kind": row[5],
            "category": row[6],
            "document_id": row[7],
            "page": props.get("page"),
            "text": props.get("text", ""),
            "vision_desc": props.get("vision_desc", ""),
            "merged": props.get("merged", ""),
            "png_path": props.get("png_path"),
            "name": props.get("name"),
            "caption": props.get("caption", ""),
            "summary": props.get("summary", ""),
            "content": props.get("content", ""),
        })
    return out


def _fetch_pages_direct(pages: list[int], machine_slug: str | None, top_k: int) -> list[dict]:
    """Directly fetch ManualSections matching specific page numbers."""
    result = proto_db.query(
        """
        MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document)-[:HAS_SECTION]->(s:ManualSection)
        WHERE s.page IN $pages
          AND ($slug IS NULL OR m.slug = $slug)
        RETURN s, m.slug AS machine_slug, m.folder AS machine_folder,
               d.name AS doc_name, d.kind AS doc_kind, d.category AS category,
               d.id AS document_id
        LIMIT $k
        """,
        {"pages": pages, "slug": machine_slug, "k": top_k},
    )
    out = []
    for row in result.result_set or []:
        node = row[0]
        props = getattr(node, "properties", {}) or {}
        out.append({
            "label": "ManualSection",
            "id": props.get("id"),
            "score": 1.0,  # direct match — boosted
            "machine_slug": row[1],
            "machine_folder": row[2],
            "doc_name": row[3],
            "doc_kind": row[4],
            "category": row[5],
            "document_id": row[6],
            "page": props.get("page"),
            "text": props.get("text", ""),
            "vision_desc": props.get("vision_desc", ""),
            "merged": props.get("merged", ""),
            "png_path": props.get("png_path"),
            "name": props.get("name"),
            "caption": "",
            "summary": "",
            "content": "",
            "match_kind": "direct_page",
        })
    return out


DEFAULT_QUOTAS = {"ManualSection": 0.6, "ConfigFile": 0.2, "ImageAsset": 0.2}

# Stopwords excluded from keyword boost
_STOP = {
    "co", "jak", "czy", "the", "and", "or", "is", "for", "was", "wie", "und",
    "der", "die", "das", "den", "dem", "bei", "mit", "von", "auf", "ich",
    "we", "you", "meldet", "sprawdzić", "zdiagnozować", "prüfen", "was",
    "how", "what", "which", "why", "gdzie", "kiedy", "kto", "ma", "mają",
    "sa", "jest", "jakie", "jaki", "jaka", "jakim", "it", "this", "that",
}


def _keyword_boost(query: str, machine_slug: str | None, limit: int = 15) -> list[dict]:
    """Boost sections whose parent Document.name matches query keywords.

    Uses FalkorDB fulltext on Document.name. Returns sections of matched
    documents with inflated scores (0.7 base + fulltext score), so they
    compete with vector hits.
    """
    # Extract meaningful terms: 3+ chars, not stopwords, or anything in quotes.
    quoted = re.findall(r'"([^"]{3,})"', query) + re.findall(r"'([^']{3,})'", query)
    raw_terms = [
        t.strip(".,;:!?()[]{}")
        for t in re.split(r"\s+", query)
        if len(t) >= 3 and t.lower().strip(".,;:!?()[]{}") not in _STOP
    ]
    # Keep distinctive tokens: uppercase acronyms, mixed-case identifiers,
    # tokens with digits, or 5+ chars.
    distinctive = [
        t for t in raw_terms
        if any(c.isupper() for c in t[1:])
        or any(c.isdigit() for c in t)
        or len(t) >= 5
    ]
    terms = list(dict.fromkeys(quoted + distinctive))
    if not terms:
        return []

    # Build RediSearch-style OR query (pipe separator, quoted multi-word)
    ft_parts = []
    for t in terms:
        safe = t.replace('"', '').replace("'", "").strip()
        if not safe:
            continue
        ft_parts.append(f'"{safe}"' if " " in safe else safe)
    ft_query = "|".join(ft_parts)

    try:
        result = proto_db.query(
            """
            CALL db.idx.fulltext.queryNodes('ManualSection', $q)
            YIELD node AS s, score AS ft_score
            MATCH (m:Machine)-[:HAS_DOCUMENT]->(d:Document)-[:HAS_SECTION]->(s)
            WHERE $slug IS NULL OR m.slug = $slug
            RETURN s, ft_score,
                   m.slug AS machine_slug, m.folder AS machine_folder,
                   d.name AS doc_name, d.kind AS doc_kind,
                   d.category AS category, d.id AS document_id
            ORDER BY ft_score DESC
            LIMIT $k
            """,
            {"q": ft_query, "slug": machine_slug, "k": limit},
        )
    except Exception as e:
        print(f"  keyword_boost fulltext error: {e}")
        return []

    out = []
    for row in result.result_set or []:
        node = row[0]
        props = getattr(node, "properties", {}) or {}
        ft = float(row[1])
        out.append({
            "label": "ManualSection",
            "id": props.get("id"),
            # Boost: base 0.6 + proportional fulltext contribution
            "score": 0.6 + min(0.35, ft / 10.0),
            "machine_slug": row[2],
            "machine_folder": row[3],
            "doc_name": row[4],
            "doc_kind": row[5],
            "category": row[6],
            "document_id": row[7],
            "page": props.get("page"),
            "text": props.get("text", ""),
            "vision_desc": props.get("vision_desc", ""),
            "merged": props.get("merged", ""),
            "png_path": props.get("png_path"),
            "match_kind": "keyword",
            "summary": "", "caption": "", "content": "", "name": None,
        })
    return out


def retrieve(query: str, *, top_k: int = 8, machine_slug: str | None = None,
             include: tuple[str, ...] = ("ManualSection", "ConfigFile", "ImageAsset"),
             quotas: dict[str, float] | None = None) -> list[dict]:
    quotas = quotas or DEFAULT_QUOTAS
    merged: list[dict] = []

    # Direct page routing — "strona 13" / "page 13" / "Seite 13"
    page_refs = extract_page_refs(query)
    direct_ids: set[str] = set()
    if page_refs:
        direct = _fetch_pages_direct(page_refs, machine_slug, top_k=len(page_refs) * 8)
        for d in direct:
            direct_ids.add(d["id"])
        merged.extend(direct)

    remaining = max(0, top_k - len(merged))
    if remaining == 0:
        return merged[:top_k]

    query_emb = generate_query_embedding(query)

    # Keyword boost via fulltext on Document names
    kw_hits = _keyword_boost(query, machine_slug, limit=max(6, top_k))
    kw_ids = {h["id"] for h in kw_hits}

    # Per-label fetch with quotas. Over-fetch then trim to quota.
    by_label: dict[str, list[dict]] = {}
    for label in include:
        items = _vector_search(label, query_emb, top_k * 3, machine_slug)
        # For ManualSection, merge kw hits; keep best score per id
        if label == "ManualSection":
            best: dict[str, dict] = {}
            for r in items + kw_hits:
                cur = best.get(r["id"])
                if cur is None or r["score"] > cur["score"]:
                    best[r["id"]] = r
            items = sorted(best.values(), key=lambda r: r["score"], reverse=True)
        by_label[label] = [r for r in items if r["id"] not in direct_ids]
    _ = kw_ids  # reserved for future usage

    # Allocate quota slots
    allocated: list[dict] = []
    used_ids: set[str] = set(direct_ids)
    for label in include:
        slots = max(1, round(remaining * quotas.get(label, 0)))
        for r in by_label[label][:slots]:
            if r["id"] in used_ids:
                continue
            allocated.append(r)
            used_ids.add(r["id"])

    # Fill any remaining slots with best leftover by raw score
    leftover: list[dict] = []
    for items in by_label.values():
        for r in items:
            if r["id"] not in used_ids:
                leftover.append(r)
    leftover.sort(key=lambda r: r["score"], reverse=True)

    combined = merged + allocated
    while len(combined) < top_k and leftover:
        combined.append(leftover.pop(0))

    # Final order: direct matches first, then by score within the rest
    direct_hits = [r for r in combined if r["id"] in direct_ids]
    other_hits = sorted(
        [r for r in combined if r["id"] not in direct_ids],
        key=lambda r: r["score"], reverse=True,
    )
    return (direct_hits + other_hits)[:top_k]


def list_machines() -> list[dict]:
    result = proto_db.query(
        """
        MATCH (m:Machine)
        OPTIONAL MATCH (m)-[:HAS_DOCUMENT]->(d:Document)
        WITH m, count(DISTINCT d) AS docs,
             sum(CASE WHEN d.kind = 'pdf' THEN 1 ELSE 0 END) AS pdfs,
             sum(CASE WHEN d.kind = 'image' THEN 1 ELSE 0 END) AS imgs,
             sum(CASE WHEN d.kind = 'text' THEN 1 ELSE 0 END) AS txts
        OPTIONAL MATCH (m)-[:HAS_DOCUMENT]->(:Document)-[:HAS_SECTION]->(s:ManualSection)
        WITH m, docs, pdfs, imgs, txts, count(s) AS sections
        RETURN m.slug AS slug, m.folder AS folder, m.type AS type,
               m.model AS model, m.serial AS serial,
               docs, pdfs, imgs, txts, sections
        ORDER BY folder
        """
    )
    return result_to_dicts(result)


def get_section(section_id: str) -> dict | None:
    result = proto_db.query(
        """
        MATCH (s:ManualSection {id: $id})<-[:HAS_SECTION]-(d:Document)<-[:HAS_DOCUMENT]-(m:Machine)
        RETURN s.id AS id, s.page AS page, s.text AS text,
               s.vision_desc AS vision_desc, s.merged AS merged,
               s.png_path AS png_path, d.name AS doc_name, d.id AS doc_id,
               m.folder AS machine, m.slug AS machine_slug
        """,
        {"id": section_id},
    )
    rows = result_to_dicts(result)
    return rows[0] if rows else None
