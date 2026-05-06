"""Gramag Hybrid Retriever — combines vector search, graph traversal, and graph vector search.

Three retrieval strategies:
1. Vector search (numpy — fast, broad): existing index
2. Graph traversal (FalkorDB — precise, relational): machine/part/service context
3. Graph vector search (FalkorDB vector index — semantic on ManualSection nodes)

Intent detection determines which strategies to use.
Graceful degradation: works with numpy-only if FalkorDB is down.
"""

import re
import numpy as np
import pickle
import os
import json
from config import INDEX_DIR, EMBED_DIMENSIONS
from embeddings import generate_query_embedding

# Lazy-loaded graph connection
_db = None


def _get_db():
    global _db
    if _db is None:
        try:
            from db import db
            db.connect()
            _db = db
        except Exception:
            _db = False  # Mark as unavailable
    return _db if _db else None


# ── Numpy Vector Index (existing) ────────────────────────────────────

_emb_matrix = None
_metadata = None


def _load_numpy_index():
    global _emb_matrix, _metadata
    if _emb_matrix is None:
        emb_path = os.path.join(INDEX_DIR, "embeddings_normed.npy")
        meta_path = os.path.join(INDEX_DIR, "metadata.pkl")
        if os.path.exists(emb_path) and os.path.exists(meta_path):
            _emb_matrix = np.load(emb_path)
            with open(meta_path, "rb") as f:
                _metadata = pickle.load(f)
    return _emb_matrix, _metadata


def vector_search(query_embedding: list[float], top_k: int = 12) -> list[dict]:
    """Search the numpy vector index. Returns list of {score, text, source, type, ...}."""
    emb_matrix, metadata = _load_numpy_index()
    if emb_matrix is None:
        return []

    qv = np.array(query_embedding, dtype=np.float32)
    qv /= np.linalg.norm(qv)
    scores = emb_matrix @ qv
    top_idx = np.argsort(scores)[-top_k:][::-1]

    results = []
    for idx in top_idx:
        chunk = metadata[idx].copy()
        chunk["score"] = float(scores[idx])
        chunk["retrieval_method"] = "vector"
        results.append(chunk)
    return results


# ── Graph Vector Search (ManualSection embeddings) ───────────────────

def graph_vector_search(query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """Search ManualSection embeddings in FalkorDB."""
    db = _get_db()
    if not db:
        return []

    try:
        from db_helpers import result_to_dicts
        result = db.query("""
            CALL db.idx.vector.queryNodes(
                'ManualSection', 'embedding', $top_k, vecf32($embedding)
            ) YIELD node, score
            RETURN node.id AS id, node.title AS title, node.summary AS summary,
                   node.supplier AS supplier, node.text AS text, node.pages AS pages,
                   score
            ORDER BY score DESC
        """, {"embedding": query_embedding, "top_k": top_k})

        rows = result_to_dicts(result)
        results = []
        for r in rows:
            results.append({
                "text": r.get("text", ""),
                "source": f"Manual: {r.get('title', '?')}",
                "type": "manual_section",
                "supplier": r.get("supplier", ""),
                "summary": r.get("summary", ""),
                "score": r.get("score", 0),
                "retrieval_method": "graph_vector",
            })
        return results
    except Exception as e:
        return []


# ── Graph Traversal ──────────────────────────────────────────────────

def get_machine_context(machine_ref: str) -> list[dict]:
    """Given a machine reference (title pattern, serial, or erp_id), get full context."""
    db = _get_db()
    if not db:
        return []

    try:
        from db_helpers import result_to_dicts

        # Try fulltext search on machine titles
        result = db.query("""
            CALL db.idx.fulltext.queryNodes('Machine', $query)
            YIELD node, score
            WITH node AS m, score
            ORDER BY score DESC LIMIT 3
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            OPTIONAL MATCH (m)-[:IS_TYPE]->(mt:MachineType)
            OPTIONAL MATCH (m)-[:MADE_BY]->(mb:MachineBrand)
            OPTIONAL MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
            WITH m, c, mt, mb, collect(sj)[0..5] AS jobs
            RETURN m.title AS machine, m.serial_number AS serial,
                   c.name AS customer, c.city AS city,
                   mt.name AS machine_type, mb.name AS brand,
                   [j IN jobs | j.title + ' (' + j.date + ')'] AS recent_jobs
        """, {"query": machine_ref})

        rows = result_to_dicts(result)
        results = []
        for r in rows:
            jobs_text = "\n".join(f"  - {j}" for j in (r.get("recent_jobs") or []))
            text = (
                f"Maschine: {r.get('machine', '?')}\n"
                f"Seriennummer: {r.get('serial', '-')}\n"
                f"Typ: {r.get('machine_type', '-')}, Marke: {r.get('brand', '-')}\n"
                f"Kunde: {r.get('customer', '-')} ({r.get('city', '-')})\n"
                f"Letzte Serviceaufträge:\n{jobs_text or '  (keine)'}"
            )
            results.append({
                "text": text,
                "source": f"Graph: Machine {r.get('machine', '?')[:40]}",
                "type": "graph_machine",
                "score": 0.9,
                "retrieval_method": "graph_traversal",
            })
        return results
    except Exception:
        return []


def get_part_context(part_ref: str) -> list[dict]:
    """Given a part reference (nummer or title pattern), get context."""
    db = _get_db()
    if not db:
        return []

    try:
        from db_helpers import result_to_dicts

        result = db.query("""
            CALL db.idx.fulltext.queryNodes('Part', $query)
            YIELD node, score
            WITH node AS p, score
            ORDER BY score DESC LIMIT 3
            WHERE NOT p.noise
            OPTIONAL MATCH (sj:ServiceJob)-[:USED_PART]->(p)
            OPTIONAL MATCH (sj)-[:FOR_MACHINE]->(m:Machine)
            WITH p, collect(DISTINCT m.title)[0..5] AS machines,
                 count(sj) AS usage_count
            OPTIONAL MATCH (p)-[:MENTIONED_IN]->(ms:ManualSection)
            RETURN p.titel AS part, p.nummer AS nummer,
                   p.manufacturer_nr AS mfr_nr,
                   machines, usage_count,
                   collect(ms.title)[0..3] AS manual_refs
        """, {"query": part_ref})

        rows = result_to_dicts(result)
        results = []
        for r in rows:
            machines_text = ", ".join(m[:40] for m in (r.get("machines") or []))
            manuals_text = ", ".join(r.get("manual_refs") or [])
            text = (
                f"Ersatzteil: [{r.get('nummer', '?')}] {r.get('part', '?')}\n"
                f"Hersteller-Nr: {r.get('mfr_nr', '-')}\n"
                f"Verwendet in {r.get('usage_count', 0)} Serviceaufträgen\n"
                f"Maschinen: {machines_text or '-'}\n"
                f"Manual-Referenzen: {manuals_text or '-'}"
            )
            results.append({
                "text": text,
                "source": f"Graph: Part [{r.get('nummer', '?')}]",
                "type": "graph_part",
                "score": 0.85,
                "retrieval_method": "graph_traversal",
            })
        return results
    except Exception:
        return []


def get_error_code_context(error_ref: str) -> list[dict]:
    """Search for error codes in the graph."""
    db = _get_db()
    if not db:
        return []

    try:
        from db_helpers import result_to_dicts

        result = db.query("""
            CALL db.idx.fulltext.queryNodes('ErrorCode', $query)
            YIELD node, score
            WITH node AS ec, score
            ORDER BY score DESC LIMIT 5
            OPTIONAL MATCH (ms:ManualSection)-[:HAS_ERROR]->(ec)
            RETURN ec.code AS code, ec.description AS description,
                   ec.cause AS cause, ec.solution AS solution,
                   ec.supplier AS supplier,
                   collect(ms.title)[0..2] AS sources
        """, {"query": error_ref})

        rows = result_to_dicts(result)
        results = []
        for r in rows:
            text = (
                f"Fehlercode {r.get('code', '?')} ({r.get('supplier', '?')})\n"
                f"Beschreibung: {r.get('description', '-')}\n"
                f"Ursache: {r.get('cause', '-')}\n"
                f"Lösung: {r.get('solution', '-')}"
            )
            results.append({
                "text": text,
                "source": f"Graph: Error {r.get('code', '?')}",
                "type": "graph_error",
                "score": 0.95,
                "retrieval_method": "graph_traversal",
            })
        return results
    except Exception:
        return []


def get_troubleshooting_context(query: str) -> list[dict]:
    """Search troubleshooting entries."""
    db = _get_db()
    if not db:
        return []

    try:
        from db_helpers import result_to_dicts

        # Search ManualSections with troubleshooting data
        result = db.query("""
            MATCH (ms:ManualSection)-[:HAS_TROUBLESHOOTING]->(te:TroubleshootingEntry)
            WHERE toLower(te.symptom) CONTAINS toLower($query)
               OR toLower(ms.summary) CONTAINS toLower($query)
            RETURN te.symptom AS symptom, te.causes AS causes,
                   te.solutions AS solutions, ms.supplier AS supplier,
                   ms.title AS source
            LIMIT 5
        """, {"query": query})

        rows = result_to_dicts(result)
        results = []
        for r in rows:
            causes = r.get("causes", "[]")
            solutions = r.get("solutions", "[]")
            try:
                causes = json.loads(causes) if isinstance(causes, str) else causes
                solutions = json.loads(solutions) if isinstance(solutions, str) else solutions
            except (json.JSONDecodeError, TypeError):
                pass
            causes_text = "\n".join(f"  - {c}" for c in (causes if isinstance(causes, list) else [causes]))
            solutions_text = "\n".join(f"  - {s}" for s in (solutions if isinstance(solutions, list) else [solutions]))
            text = (
                f"Symptom: {r.get('symptom', '?')}\n"
                f"Ursachen:\n{causes_text}\n"
                f"Lösungen:\n{solutions_text}"
            )
            results.append({
                "text": text,
                "source": f"Graph: Troubleshooting ({r.get('supplier', '?')})",
                "type": "graph_troubleshooting",
                "score": 0.9,
                "retrieval_method": "graph_traversal",
            })
        return results
    except Exception:
        return []


# ── Intent Detection ─────────────────────────────────────────────────

# Patterns for detecting what the user is asking about
MACHINE_PATTERNS = [
    r'\b(Falzmaschine|Kuvertieranlage|Folieranlage|Kreuzbruch|Fördersystem|Etikettiermaschine|Falzanlage)\b',
    r'\b(MBO|CMC|Baumer|Avery|BDT|Beck|HAPA)\b',
    r'\b(CMC\s*\d+|MBO\s+\w+)',  # "CMC 250", "MBO T52"
    r'\b[A-Z]{1,4}\d{1,4}[A-Za-z]?\b',  # Model numbers: T800, K80, K800, XTS2, C221
    r'\bMaschine\b',
    r'\bmachine\b',
]

PART_PATTERNS = [
    r'\bErsatzteile?\b',
    r'\bTeile?\b',
    r'\bpart[s]?\b',
    r'\b\d{5}\b',  # 5-digit part numbers
    r'\bHersteller-Nr\b',
]

ERROR_PATTERNS = [
    r'\bStörung\b',
    r'\bFehler(code|meldung)?\b',
    r'\berror\b',
    r'\bfault\b',
    r'\bE\d{2,3}\b',  # Error codes like E01, E102
]

TROUBLESHOOT_PATTERNS = [
    r'\bwas tun\b',
    r'\bwhat to do\b',
    r'\bproblem\b',
    r'\btroublesho',
    r'\bfunktioniert nicht\b',
    r'\bnicht richtig\b',
    r'\bdefekt\b',
]


def detect_intent(query: str) -> dict:
    """Detect query intent and extract specific entities for graph queries."""
    intent = {
        "machine": any(re.search(p, query, re.IGNORECASE) for p in MACHINE_PATTERNS),
        "part": any(re.search(p, query, re.IGNORECASE) for p in PART_PATTERNS),
        "error": any(re.search(p, query, re.IGNORECASE) for p in ERROR_PATTERNS),
        "troubleshoot": any(re.search(p, query, re.IGNORECASE) for p in TROUBLESHOOT_PATTERNS),
    }

    # Extract specific entities for graph fulltext search
    # Machine references: brand names, model codes, machine types
    machine_refs = []
    for p in MACHINE_PATTERNS:
        for m in re.finditer(p, query, re.IGNORECASE):
            machine_refs.append(m.group())
    intent["machine_refs"] = machine_refs

    # Part references: 5-digit numbers
    part_refs = re.findall(r'\b\d{5}\b', query)
    intent["part_refs"] = part_refs

    # Error code references
    error_refs = re.findall(r'\bE\d{2,3}\b', query)
    intent["error_refs"] = error_refs

    # Full query for fallback
    intent["query"] = query

    return intent


# ── Main Retrieval ───────────────────────────────────────────────────

def retrieve(query: str, top_k: int = 12) -> list[dict]:
    """Hybrid retrieval: combine vector search + graph traversal based on intent.

    Returns a merged, deduplicated list of results sorted by relevance.
    """
    intent = detect_intent(query)
    results = []

    # Always do vector search (fast, broad)
    query_emb = generate_query_embedding(query)
    vector_results = vector_search(query_emb, top_k=top_k)
    results.extend(vector_results)

    # Graph-based retrieval based on intent — use extracted entities
    if intent["machine"]:
        # Search with each extracted machine ref, then fallback to full query
        searched = set()
        for ref in intent["machine_refs"]:
            if ref.lower() not in searched:
                searched.add(ref.lower())
                results.extend(get_machine_context(ref))
        if not searched:
            results.extend(get_machine_context(query))

    if intent["part"]:
        for ref in intent["part_refs"]:
            results.extend(get_part_context(ref))
        if not intent["part_refs"]:
            # Try with machine refs — "Teile für K800" -> search parts used with K800
            for ref in intent["machine_refs"]:
                results.extend(get_part_context(ref))

    if intent["error"]:
        for ref in intent["error_refs"]:
            results.extend(get_error_code_context(ref))
        if not intent["error_refs"]:
            results.extend(get_error_code_context(query))

    if intent["troubleshoot"]:
        results.extend(get_troubleshooting_context(query))

    # Graph vector search on ManualSections (semantic)
    graph_vs = graph_vector_search(query_emb, top_k=5)
    results.extend(graph_vs)

    # Deduplicate by text similarity (simple: first 100 chars)
    seen = set()
    unique = []
    for r in results:
        key = r.get("text", "")[:100]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    # Sort by score descending
    unique.sort(key=lambda r: r.get("score", 0), reverse=True)

    return unique[:top_k + 5]  # Return slightly more than top_k to let caller choose
