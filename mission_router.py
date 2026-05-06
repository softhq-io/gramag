"""Gramag Einsatzplaner — Mission API router."""

import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, CHAT_MODEL
from auth import get_current_user
import mission

router = APIRouter(prefix="/api/mission", tags=["mission"])

_client = genai.Client(api_key=GEMINI_API_KEY)


class BriefingRequest(BaseModel):
    machine_erp_id: str
    symptom: str = ""


class AskRequest(BaseModel):
    query: str


@router.get("/search")
def search(
    q: str = Query(..., min_length=1),
    type: str = Query("machine", pattern="^(machine|customer)$"),
    limit: int = Query(10, ge=1, le=50),
    _user: dict = Depends(get_current_user),
):
    if type == "customer":
        return mission.search_customers(q, limit=limit)
    return mission.search_machines(q, limit=limit)


@router.get("/machine/{erp_id}")
def machine_detail(erp_id: str, _user: dict = Depends(get_current_user)):
    detail = mission.get_machine_detail(erp_id)
    if not detail:
        return {"error": "Machine not found"}
    return detail


@router.post("/briefing")
def briefing(req: BriefingRequest, _user: dict = Depends(get_current_user)):
    return mission.generate_briefing(req.machine_erp_id, req.symptom)


@router.get("/machine/{erp_id}/history")
def service_history(
    erp_id: str,
    limit: int = Query(20, ge=1, le=100),
    _user: dict = Depends(get_current_user),
):
    return mission.get_service_history(erp_id, limit=limit)


@router.get("/machine/{erp_id}/parts-kit")
def parts_kit(erp_id: str, _user: dict = Depends(get_current_user)):
    return mission.build_parts_kit(erp_id)


@router.get("/machine/{erp_id}/similar-cases")
def similar_cases(
    erp_id: str,
    symptom: str = "",
    limit: int = Query(8, ge=1, le=30),
    _user: dict = Depends(get_current_user),
):
    return mission.find_similar_cases(erp_id, symptom=symptom, limit=limit)


@router.post("/ask")
def free_ask(req: AskRequest, _user: dict = Depends(get_current_user)):
    """Free-form Q&A — no machine selection required."""
    from retriever import retrieve, detect_intent

    results = retrieve(req.query, top_k=12)
    intent = detect_intent(req.query)

    graph_parts, vector_parts, sources = [], [], []
    for i, r in enumerate(results[:15]):
        src = r.get("source", "?")
        ctype = r.get("type", "?")
        method = r.get("retrieval_method", "?")
        text = r.get("text", "")[:600]
        label = f"[{i+1}] ({ctype}, {method}) {src}"
        supplier = r.get("supplier", "")
        if supplier:
            label += f" [{supplier}]"
        entry = f"{label}\n{text}"
        (graph_parts if method == "graph_traversal" else vector_parts).append(entry)
        pdf_url = None
        try:
            from server import _pdf_path_map
            fname = src.replace("PDF: ", "").replace("Manual: ", "").split(" p.")[0]
            rel_path = _pdf_path_map.get(fname.lower())
            if rel_path:
                from urllib.parse import quote
                page = None
                if r.get("pages"):
                    page = r["pages"][0] if isinstance(r["pages"], list) else r["pages"]
                elif " p." in src:
                    page = src.split(" p.")[-1].split("-")[0]
                frag = f"#page={page}" if page else ""
                pdf_url = f"/api/pdfs/{quote(rel_path)}{frag}"
        except ImportError:
            pass

        sources.append({
            "rank": i + 1, "source": src, "type": ctype,
            "method": method, "score": round(r.get("score", 0), 3),
            "text": " ".join(r.get("text", "").split())[:200],
            "pdf_url": pdf_url,
        })

    graph_ctx = "\n\n---\n\n".join(graph_parts) if graph_parts else "(keine Graph-Daten gefunden)"
    vector_ctx = "\n\n---\n\n".join(vector_parts[:10])

    prompt = f"""You are a technical assistant for Gramag Grafische Maschinen AG, a Swiss company servicing printing, folding, cutting, enveloping, and labelling machines.

You have access to their ERP knowledge graph (machines, customers, spare parts, service history) and technical manuals from suppliers.

RULES:
- Answer ONLY from context. Do not invent.
- Reference sources [1],[2] etc.
- Same language as question.
- Be concise, practical — for service technicians.
- Include part numbers when found.

GRAPH CONTEXT:
{graph_ctx}

DOCUMENT CONTEXT:
{vector_ctx}

DETECTED INTENT: {json.dumps(intent)}

QUESTION: {req.query}"""

    resp = _client.models.generate_content(
        model=CHAT_MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=2000),
    )

    return {"answer": resp.text, "sources": sources}
