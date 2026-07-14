"""Gramag Einsatzplaner — Mission API router."""

import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from ai_client import chat
from auth import get_current_user
from authorization import require_erp_machine
import mission

router = APIRouter(prefix="/api/mission", tags=["mission"])


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
    user: dict = Depends(get_current_user),
):
    if type == "customer":
        return mission.search_customers(q, limit=limit, user=user)
    return mission.search_machines(q, limit=limit, user=user)


@router.get("/machine/{erp_id}")
def machine_detail(erp_id: str, user: dict = Depends(get_current_user)):
    require_erp_machine(user, erp_id)
    detail = mission.get_machine_detail(erp_id)
    if not detail:
        return {"error": "Machine not found"}
    return detail


@router.post("/briefing")
def briefing(req: BriefingRequest, user: dict = Depends(get_current_user)):
    require_erp_machine(user, req.machine_erp_id)
    return mission.generate_briefing(req.machine_erp_id, req.symptom)


@router.get("/machine/{erp_id}/history")
def service_history(
    erp_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    require_erp_machine(user, erp_id)
    return mission.get_service_history(erp_id, limit=limit)


@router.get("/machine/{erp_id}/parts-kit")
def parts_kit(erp_id: str, user: dict = Depends(get_current_user)):
    require_erp_machine(user, erp_id)
    return mission.build_parts_kit(erp_id)


@router.post("/ask")
def free_ask(req: AskRequest, user: dict = Depends(get_current_user)):
    """Free-form Q&A — no machine selection required."""
    from retriever import retrieve, detect_intent

    if user.get("all_clients"):
        results = retrieve(req.query, top_k=12)
    else:
        from proto.retriever import retrieve as proto_retrieve
        hits = proto_retrieve(
            req.query, top_k=12, all_clients=False,
            client_ids=list(user.get("client_ids") or []),
        )
        results = [
            {
                **hit,
                "source": f"Manual: {hit.get('doc_name', '?')} p.{hit.get('page', '?')}",
                "type": "manual_section",
                "retrieval_method": "proto_vector",
                "text": hit.get("merged") or hit.get("text") or hit.get("vision_desc") or "",
                "supplier": hit.get("machine_slug", ""),
            }
            for hit in hits
        ]
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

    answer = chat(prompt, temperature=0.2, max_tokens=2000)

    return {"answer": answer, "sources": sources}
