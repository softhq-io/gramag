"""FastAPI router for the multimodal prototype — mounted under /api/proto."""

import html
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from auth import get_current_user
from authorization import (
    require_proto_asset,
    require_proto_chat,
    require_proto_document,
    require_proto_machine,
    require_proto_section,
)
from proto import resolve_cache, resolve_source
from proto.answer import chat_answer as generate_chat_answer
from proto.chat_store import (
    append_message,
    create_session,
    get_session,
    list_messages,
    list_sessions,
    retrieve_memory,
)
from proto.retriever import list_machines

router = APIRouter(prefix="/api/proto", tags=["proto"])


class ProtoQuery(BaseModel):
    query: str
    machine_slug: str | None = None
    customer: str | None = None
    top_k: int = 6
    deep: bool = False


class ProtoChatCreate(BaseModel):
    machine_slug: str | None = None
    customer: str | None = None
    title: str | None = None


class ProtoChatMessageRequest(BaseModel):
    text: str
    top_k: int = 6
    deep: bool = False


@router.post("/ask")
def ask(q: ProtoQuery, user: dict = Depends(get_current_user)):
    machine = require_proto_machine(user, q.machine_slug) if q.machine_slug else None
    session = create_session(
        machine_slug=q.machine_slug,
        customer=(machine or {}).get("customer"),
        client_id=(machine or {}).get("client_id"),
        title=q.query,
        user=user,
    )
    append_message(session_id=session["id"], role="user", text=q.query)
    messages = list_messages(session["id"])
    memories = retrieve_memory(query=q.query, session=session, user=user)
    result = generate_chat_answer(
        q.query,
        transcript=messages,
        memories=memories,
        machine_slug=q.machine_slug,
        customer=(machine or {}).get("customer"),
        top_k=q.top_k,
        deep=q.deep,
        all_clients=bool(user.get("all_clients")),
        client_ids=list(user.get("client_ids") or []),
    )
    assistant_message = append_message(
        session_id=session["id"],
        role="assistant",
        text=result["answer"],
        model=result.get("model"),
        citations=result.get("citations"),
        hits=result.get("hits"),
    )
    return {
        **result,
        "chat_session_id": session["id"],
        "assistant_message_id": assistant_message["id"],
    }


@router.post("/chats")
def create_chat(req: ProtoChatCreate, current_user: dict = Depends(get_current_user)):
    machine = require_proto_machine(current_user, req.machine_slug) if req.machine_slug else None
    session = create_session(
        machine_slug=req.machine_slug,
        customer=(machine or {}).get("customer"),
        client_id=(machine or {}).get("client_id"),
        title=req.title,
        user=current_user,
    )
    return session


@router.get("/chats")
def chats(
    machine_slug: str | None = None,
    customer: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    machine = require_proto_machine(current_user, machine_slug) if machine_slug else None
    return list_sessions(
        machine_slug=machine_slug,
        customer=(machine or {}).get("customer") if machine_slug else None,
        user=current_user,
    )


@router.get("/chats/{chat_id}")
def chat(chat_id: str, current_user: dict = Depends(get_current_user)):
    require_proto_chat(current_user, chat_id)
    return {
        "session": get_session(chat_id, user=current_user),
        "messages": list_messages(chat_id),
    }


@router.post("/chats/{chat_id}/messages")
def chat_message(
    chat_id: str,
    req: ProtoChatMessageRequest,
    current_user: dict = Depends(get_current_user),
):
    session = require_proto_chat(current_user, chat_id)
    if int(session.get("isolation_version") or 0) < 2:
        raise HTTPException(409, "Legacy chats are read-only; create a new machine-scoped chat")
    if not session.get("owned_by_me"):
        raise HTTPException(403, "Other users' chats are read-only")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Message text is required")

    user_message = append_message(
        session_id=chat_id,
        role="user",
        text=text,
    )
    transcript = list_messages(chat_id)
    memories = retrieve_memory(query=text, session=session, user=current_user)
    result = generate_chat_answer(
        text,
        transcript=transcript,
        memories=memories,
        machine_slug=session.get("machine_slug"),
        customer=session.get("customer"),
        top_k=req.top_k,
        deep=req.deep,
        all_clients=bool(current_user.get("all_clients")),
        client_ids=list(current_user.get("client_ids") or []),
    )
    assistant_message = append_message(
        session_id=chat_id,
        role="assistant",
        text=result["answer"],
        model=result.get("model"),
        citations=result.get("citations"),
        hits=result.get("hits"),
    )
    return {
        "session": get_session(chat_id, user=current_user),
        "user_message": user_message,
        "assistant_message": assistant_message,
        "answer": result["answer"],
        "citations": result.get("citations", []),
        "hits": result.get("hits", []),
        "model": result.get("model"),
        "memory_count": result.get("memory_count", 0),
    }


@router.get("/machines")
def machines(current_user: dict = Depends(get_current_user)):
    return list_machines(
        all_clients=bool(current_user.get("all_clients")),
        client_ids=list(current_user.get("client_ids") or []),
    )


@router.get("/customer")
def customer_overview(current_user: dict = Depends(get_current_user)):
    """Customer info + aggregated KB stats for the Proto graph."""
    from db_helpers import result_to_dicts
    from proto.db_proto import proto_db

    machines = list_machines(
        all_clients=bool(current_user.get("all_clients")),
        client_ids=list(current_user.get("client_ids") or []),
    )
    total_pages = sum(m.get("sections") or 0 for m in machines)
    total_imgs = sum(m.get("imgs") or 0 for m in machines)
    total_cfgs = sum(m.get("txts") or 0 for m in machines)
    total_docs = sum(m.get("docs") or 0 for m in machines)

    # Hersteller heuristic from machine type
    HERSTELLER_MAP = {
        "smb": "Ferag",
        "adressiersystem": "Avery / Baumer hhs",
        "folieneinschlag": "CMC Italy",
        "falzmaschine": "MBO",
        "inkjet": "Buhrs / Atlantic Zeiser",
        "kreuzleger": "Rima",
        "sigma": "Sigma",
        "jetvision": "JetVision",
        "beorda": "Eigenbau Beorda",
    }
    for m in machines:
        slug = (m.get("slug") or "").lower()
        type_ = (m.get("type") or "").lower()
        hersteller = "Sonstige"
        for key, name in HERSTELLER_MAP.items():
            if key in slug or key in type_:
                hersteller = name
                break
        m["hersteller"] = hersteller

    # Attach sample documents per machine for smart suggestions
    for m in machines:
        try:
            r = proto_db.query(
                """
                MATCH (mach:Machine {slug: $slug})-[:HAS_DOCUMENT]->(d:Document)
                OPTIONAL MATCH (d)-[:HAS_SECTION]->(s:ManualSection)
                WITH d, count(s) AS pages
                WHERE pages > 0 OR d.kind <> 'pdf'
                RETURN d.name AS name, d.kind AS kind, d.category AS category, pages
                ORDER BY pages DESC, d.name
                LIMIT 8
                """,
                {"slug": m["slug"]},
            )
            m["sample_docs"] = result_to_dicts(r)
        except Exception:
            m["sample_docs"] = []

    customer_names = sorted({m.get("customer") for m in machines if m.get("customer")})
    if len(customer_names) == 1:
        customer_name = customer_names[0]
        tagline = "Technische Dokumentation und Servicewissen"
    elif len(customer_names) > 1:
        customer_name = "Kundendienst Kunden"
        tagline = f"{len(customer_names)} Kunden mit technischer Dokumentation"
    else:
        customer_name = "Proto Knowledge Base"
        tagline = "Technische Dokumentation und Servicewissen"

    return {
        "customer": {
            "name": customer_name,
            "tagline": tagline,
            "machine_count": len(machines),
        },
        "stats": {
            "machines": len(machines),
            "documents": total_docs,
            "pages": total_pages,
            "images": total_imgs,
            "configs": total_cfgs,
        },
        "machines": machines,
    }


@router.get("/section/{section_id}")
def section(section_id: str, current_user: dict = Depends(get_current_user)):
    return require_proto_section(current_user, section_id)


@router.get("/page-image/{section_id}")
def page_image(section_id: str, current_user: dict = Depends(get_current_user)):
    s = require_proto_section(current_user, section_id)
    png = resolve_cache(s.get("png_path") or "")
    if not png or not os.path.exists(png):
        raise HTTPException(404, "PNG missing")
    return FileResponse(png, media_type="image/png")


@router.get("/view/{doc_id}", response_class=HTMLResponse)
def document_viewer(
    doc_id: str,
    page: int = 1,
    current_user: dict = Depends(get_current_user),
):
    """HTML wrapper that embeds the PDF with an explicit page jump.

    Works reliably across Chrome/Safari/Firefox — the nested iframe receives
    the `#page=N` fragment, which the browser's PDF plugin honors.
    """
    document = require_proto_document(current_user, doc_id)
    name = html.escape(document.get("name") or "document")
    safe_doc_id = quote(doc_id, safe="")
    kind = document.get("kind")
    page = max(1, int(page))
    src = f"/api/proto/document/{safe_doc_id}#page={page}&zoom=page-fit&view=FitH"

    if kind != "pdf":
        # Non-PDF: just redirect to raw file
        return HTMLResponse(
            f"<!doctype html><meta http-equiv='refresh' content='0; url=/api/proto/document/{safe_doc_id}'>"
        )

    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{name} — p.{page}</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; height: 100%; background: #1b1f28; color: #e1e4e8; font-family: -apple-system, BlinkMacSystemFont, sans-serif; }}
    body {{ display: grid; min-height: 100dvh; grid-template-rows: auto minmax(0, 1fr); }}
    header {{ position: sticky; z-index: 10; top: 0; display: grid; min-height: 64px; padding: 8px 12px; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; border-bottom: 1px solid #30363d; background: #161b22; }}
    header .title {{ min-width: 0; overflow: hidden; color: #c9d1d9; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }}
    header button, header a {{ display: inline-flex; min-width: 44px; min-height: 44px; padding: 0 13px; align-items: center; justify-content: center; border: 1px solid #3b4655; border-radius: 9px; background: #202936; color: #d9e2ec; cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; text-decoration: none; }}
    header a {{ border-color: #12bdd5; background: rgba(18, 189, 213, 0.14); color: #5de4f4; }}
    header button:hover, header button:focus-visible, header a:hover, header a:focus-visible {{ border-color: #5de4f4; outline: none; }}
    iframe {{ width: 100%; height: 100%; min-height: 0; border: 0; background: #fff; }}
    @media (max-width: 620px) {{
      header {{ min-height: 70px; padding: 8px; gap: 7px; }}
      header button {{ width: 44px; padding: 0; font-size: 0; }}
      header button::before {{ content: '←'; font-size: 20px; }}
      header a {{ padding: 0 11px; }}
      header a .wide {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <button type="button" onclick="if (history.length > 1) history.back(); else location.href='/einsatzplaner/proto'">Zurück</button>
    <span class="title" title="{name}">{name} — Seite {page}</span>
    <a href="/api/proto/document/{safe_doc_id}"><span class="wide">Original-</span>PDF öffnen</a>
  </header>
  <iframe src="{src}" title="PDF"></iframe>
</body>
</html>""")


@router.get("/document/{doc_id}")
def document_file(doc_id: str, current_user: dict = Depends(get_current_user)):
    document = require_proto_document(current_user, doc_id)
    p = resolve_source(document.get("path") or "")
    name = document.get("name") or "document"
    kind = document.get("kind")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "File missing")

    ext = Path(p).suffix.lower()
    mime = {
        ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")

    # Inline PDFs and text so they render in the browser tab.
    disp = "inline" if kind in ("pdf", "text", "image") else "attachment"
    return FileResponse(
        p, media_type=mime,
        headers={"Content-Disposition": f'{disp}; filename="{name}"'},
    )


@router.get("/asset-image/{asset_id}")
def asset_image(asset_id: str, current_user: dict = Depends(get_current_user)):
    asset = require_proto_asset(current_user, asset_id)
    p = resolve_source(asset.get("path") or "")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "File missing")
    ext = Path(p).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "bmp": "image/bmp"}.get(ext, "application/octet-stream")
    if ext == "pcx":
        from PIL import Image
        import io
        img = Image.open(p).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        from fastapi.responses import Response
        return Response(content=buf.getvalue(), media_type="image/png")
    return FileResponse(p, media_type=mime)


@router.get("/stats")
def stats(current_user: dict = Depends(get_current_user)):
    from proto.db_proto import proto_db
    if current_user.get("all_clients"):
        return proto_db.stats()
    machines = list_machines(all_clients=False, client_ids=current_user.get("client_ids") or [])
    return {
        "nodes": {
            "Machine": len(machines),
            "Document": sum(int(m.get("docs") or 0) for m in machines),
            "ManualSection": sum(int(m.get("sections") or 0) for m in machines),
        },
        "relationships": {},
    }
