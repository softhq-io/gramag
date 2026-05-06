"""ERP graph endpoints — machine/part/service lookups + hybrid Q&A.

Combines ERP graph traversal (gramag graph) with proto KB vector search
(gramag_proto graph) for hybrid retrieval without numpy index.
"""

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from auth import get_current_user
from db import db
from db_helpers import result_to_dicts, result_single
from config import GEMINI_API_KEY, CHAT_MODEL

router = APIRouter(prefix="/api/erp", tags=["erp"])


# ── Graph Stats ─────────────────────────────────────────────────────

@router.get("/stats")
def erp_stats():
    try:
        return db.stats()
    except Exception as e:
        return {"error": str(e)}


# ── Machine ─────────────────────────────────────────────────────────

@router.get("/machines")
def list_machines(_user: dict = Depends(get_current_user)):
    result = db.query("""
        MATCH (m:Machine)
        OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
        OPTIONAL MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
        WITH m, c, count(sj) AS job_count
        RETURN m.erp_id AS erp_id, m.title AS title,
               m.serial_number AS serial_number,
               c.name AS customer, c.erp_id AS customer_erp_id,
               job_count
        ORDER BY m.title
    """)
    return result_to_dicts(result)


@router.get("/machine/{erp_id}")
def machine_detail(erp_id: str, _user: dict = Depends(get_current_user)):
    result = db.query("""
        MATCH (m:Machine {erp_id: $erp_id})
        OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
        OPTIONAL MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
        WITH m, c, count(sj) AS job_count
        RETURN m.erp_id AS erp_id, m.title AS title,
               m.serial_number AS serial_number,
               m.new_erp_nummer AS nummer,
               c.name AS customer, c.erp_id AS customer_erp_id,
               job_count
    """, {"erp_id": erp_id})
    row = result_single(result)
    if not row:
        return {"error": "Machine not found"}
    return row


@router.get("/machine/{erp_id}/history")
def service_history(
    erp_id: str,
    limit: int = Query(20, ge=1, le=100),
    _user: dict = Depends(get_current_user),
):
    result = db.query("""
        MATCH (m:Machine {erp_id: $erp_id})
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
        OPTIONAL MATCH (sc:ServiceComment)-[:ON_JOB]->(sj)
        OPTIONAL MATCH (sj)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        WITH sj,
             collect(DISTINCT {text: sc.text, date: sc.date})[0..5] AS comments,
             collect(DISTINCT {nummer: p.nummer, titel: p.titel})[0..10] AS parts
        ORDER BY sj.date DESC
        LIMIT $limit
        RETURN sj.erp_id AS erp_id, sj.title AS title,
               sj.nummer AS nummer, sj.date AS date,
               sj.description AS description,
               comments, parts
    """, {"erp_id": erp_id, "limit": limit})
    return result_to_dicts(result)


@router.get("/machine/{erp_id}/parts")
def machine_parts(erp_id: str, _user: dict = Depends(get_current_user)):
    result = db.query("""
        MATCH (m:Machine {erp_id: $erp_id})
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
        MATCH (sj)-[u:USED_PART]->(p:Part)
        WHERE NOT p.noise
        WITH p, count(sj) AS frequency,
             collect(DISTINCT sj.title)[0..3] AS job_titles
        RETURN p.nummer AS nummer, p.titel AS titel,
               p.manufacturer_nr AS manufacturer_nr,
               frequency, job_titles
        ORDER BY frequency DESC
        LIMIT 30
    """, {"erp_id": erp_id})
    return result_to_dicts(result)


# ── Part ────────────────────────────────────────────────────────────

@router.get("/part/{nummer}")
def part_detail(nummer: str, _user: dict = Depends(get_current_user)):
    result = db.query("""
        MATCH (p:Part {nummer: $nummer})
        OPTIONAL MATCH (sj:ServiceJob)-[u:USED_PART]->(p)
        OPTIONAL MATCH (sj)-[:FOR_MACHINE]->(m:Machine)
        WITH p,
             collect(DISTINCT {machine: m.title, machine_erp_id: m.erp_id,
                               job: sj.title, date: sj.date})[0..15] AS usage,
             count(sj) AS usage_count
        RETURN p.titel AS titel, p.nummer AS nummer,
               p.manufacturer_nr AS manufacturer_nr,
               usage_count, usage
    """, {"nummer": nummer})
    row = result_single(result)
    if not row:
        return {"error": "Part not found"}
    return row


# ── Search ──────────────────────────────────────────────────────────

@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    _user: dict = Depends(get_current_user),
):
    machines = []
    try:
        r = db.query("""
            CALL db.idx.fulltext.queryNodes('Machine', $q)
            YIELD node, score
            WITH node AS m, score ORDER BY score DESC LIMIT 5
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            RETURN m.erp_id AS erp_id, m.title AS title,
                   c.name AS customer, score
        """, {"q": q})
        machines = result_to_dicts(r)
    except Exception:
        pass

    parts = []
    try:
        r = db.query("""
            CALL db.idx.fulltext.queryNodes('Part', $q)
            YIELD node, score
            WITH node AS p, score ORDER BY score DESC LIMIT 5
            WHERE NOT p.noise
            RETURN p.nummer AS nummer, p.titel AS titel, score
        """, {"q": q})
        parts = result_to_dicts(r)
    except Exception:
        pass

    return {"machines": machines, "parts": parts}


# ── Hybrid Q&A ──────────────────────────────────────────────────────

class AskRequest(BaseModel):
    query: str
    machine_erp_id: str | None = None


def _erp_graph_context(query: str, machine_erp_id: str | None = None) -> list[dict]:
    """Graph traversal on ERP data for machines, parts, service history."""
    results = []

    if machine_erp_id:
        try:
            r = db.query("""
                MATCH (m:Machine {erp_id: $erp_id})
                OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
                OPTIONAL MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
                OPTIONAL MATCH (sj)-[:USED_PART]->(p:Part)
                WHERE NOT p.noise
                OPTIONAL MATCH (sc:ServiceComment)-[:ON_JOB]->(sj)
                WITH m, c,
                     collect(DISTINCT {title: sj.title, date: sj.date, nummer: sj.nummer})[0..8] AS jobs,
                     collect(DISTINCT {titel: p.titel, nummer: p.nummer})[0..15] AS parts,
                     collect(DISTINCT sc.text)[0..5] AS comments
                RETURN m.title AS machine, m.serial_number AS serial,
                       c.name AS customer, jobs, parts, comments
            """, {"erp_id": machine_erp_id})
            for row in result_to_dicts(r):
                jobs_text = "\n".join(f"  - {j.get('title', '?')} ({j.get('date', '?')})" for j in (row.get("jobs") or []))
                parts_text = ", ".join(f"{p.get('nummer', '?')} ({p.get('titel', '')})" for p in (row.get("parts") or []))
                comments_text = "\n".join(f"  - {c[:200]}" for c in (row.get("comments") or []) if c)
                text = (
                    f"Maschine: {row.get('machine', '?')}\n"
                    f"Seriennummer: {row.get('serial', '-')}\n"
                    f"Kunde: {row.get('customer', '-')}\n"
                    f"Serviceaufträge:\n{jobs_text or '  (keine)'}\n"
                    f"Verwendete Teile: {parts_text or 'keine'}\n"
                    f"Kommentare:\n{comments_text or '  (keine)'}"
                )
                results.append({
                    "text": text,
                    "source": f"ERP: {row.get('machine', '?')[:40]}",
                    "type": "erp_machine",
                    "score": 0.95,
                    "method": "graph_traversal",
                })
        except Exception:
            pass
        return results

    try:
        from retriever import detect_intent, get_machine_context, get_part_context
        intent = detect_intent(query)
        if intent["machine"]:
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
    except Exception:
        pass
    return results


def _proto_kb_context(query: str, top_k: int = 6) -> list[dict]:
    """Vector search on proto KB (supplier manuals)."""
    try:
        from proto.retriever import retrieve as proto_retrieve
        hits = proto_retrieve(query, top_k=top_k)
        results = []
        for h in hits:
            text = h.get("merged") or h.get("text") or h.get("vision_desc") or ""
            if not text.strip():
                continue
            results.append({
                "text": text[:800],
                "source": f"Manual: {h.get('doc_name', '?')} p.{h.get('page', '?')}",
                "type": "manual_section",
                "supplier": h.get("machine_slug", ""),
                "score": h.get("score", 0),
                "method": "proto_vector",
                "section_id": h.get("id"),
            })
        return results
    except Exception:
        return []


@router.post("/ask")
def ask(req: AskRequest, _user: dict = Depends(get_current_user)):
    """Hybrid Q&A: ERP graph traversal + proto KB vector search."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    erp_results = _erp_graph_context(req.query, req.machine_erp_id)
    kb_results = _proto_kb_context(req.query, top_k=6)

    erp_parts = []
    kb_parts = []
    sources = []

    for i, r in enumerate(erp_results + kb_results):
        label = f"[{i+1}] ({r.get('type', '?')}) {r.get('source', '?')}"
        entry = f"{label}\n{r.get('text', '')[:600]}"
        if r.get("method") == "graph_traversal":
            erp_parts.append(entry)
        else:
            kb_parts.append(entry)
        sources.append({
            "rank": i + 1,
            "source": r.get("source", "?"),
            "type": r.get("type", "?"),
            "method": r.get("method", "?"),
            "score": round(r.get("score", 0), 3),
            "text": r.get("text", "")[:200],
            "section_id": r.get("section_id"),
        })

    erp_ctx = "\n\n---\n\n".join(erp_parts) if erp_parts else "(keine ERP-Daten gefunden)"
    kb_ctx = "\n\n---\n\n".join(kb_parts[:8]) if kb_parts else "(keine Manual-Daten gefunden)"

    prompt = f"""You are a technical assistant for Gramag Grafische Maschinen AG, a Swiss company servicing printing, folding, cutting, enveloping, and labelling machines.

You have access to their ERP data (machines, service history, spare parts) and technical manuals from suppliers.

RULES:
- Answer ONLY from context. Do not invent.
- Reference sources [1],[2] etc.
- Same language as question.
- Be concise, practical — for service technicians.
- Include part numbers when found.

ERP CONTEXT (service history, machine data, parts):
{erp_ctx}

TECHNICAL MANUALS (supplier documentation):
{kb_ctx}

QUESTION: {req.query}"""

    resp = client.models.generate_content(
        model=CHAT_MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=2000),
    )

    return {
        "answer": resp.text,
        "sources": sources,
        "erp_results": len(erp_parts),
        "kb_results": len(kb_parts),
    }
