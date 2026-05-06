"""FastAPI router for the multimodal prototype — mounted under /api/proto."""

import html
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from proto import resolve_cache, resolve_source
from proto.answer import answer as generate_answer
from proto.retriever import get_section, list_machines

router = APIRouter(prefix="/api/proto", tags=["proto"])


class ProtoQuery(BaseModel):
    query: str
    machine_slug: str | None = None
    top_k: int = 6
    deep: bool = False


@router.post("/ask")
def ask(q: ProtoQuery):
    return generate_answer(
        q.query, machine_slug=q.machine_slug, top_k=q.top_k, deep=q.deep,
    )


@router.get("/machines")
def machines():
    return list_machines()


@router.get("/customer")
def customer_overview():
    """Static customer info + aggregated KB stats. Single-customer prototype."""
    from db_helpers import result_to_dicts
    from proto.db_proto import proto_db

    machines = list_machines()
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

    return {
        "customer": {
            "name": "Beorda Direktwerbung AG",
            "tagline": "Spezialist für personalisierte Direktwerbung",
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
def section(section_id: str):
    s = get_section(section_id)
    if not s:
        raise HTTPException(404, "Section not found")
    return s


@router.get("/page-image/{section_id}")
def page_image(section_id: str):
    s = get_section(section_id)
    if not s:
        raise HTTPException(404, "Section not found")
    png = resolve_cache(s.get("png_path") or "")
    if not png or not os.path.exists(png):
        raise HTTPException(404, "PNG missing")
    return FileResponse(png, media_type="image/png")


@router.get("/view/{doc_id}", response_class=HTMLResponse)
def document_viewer(doc_id: str, page: int = 1):
    """HTML wrapper that embeds the PDF with an explicit page jump.

    Works reliably across Chrome/Safari/Firefox — the nested iframe receives
    the `#page=N` fragment, which the browser's PDF plugin honors.
    """
    from db_helpers import result_to_dicts
    from proto.db_proto import proto_db
    result = proto_db.query(
        "MATCH (d:Document {id: $id}) RETURN d.name AS name, d.kind AS kind",
        {"id": doc_id},
    )
    rows = result_to_dicts(result)
    if not rows:
        raise HTTPException(404, "Document not found")
    name = html.escape(rows[0].get("name") or "document")
    kind = rows[0].get("kind")
    page = max(1, int(page))
    src = f"/api/proto/document/{doc_id}#page={page}&zoom=page-fit&view=FitH"

    if kind != "pdf":
        # Non-PDF: just redirect to raw file
        return HTMLResponse(
            f"<!doctype html><meta http-equiv='refresh' content='0; url=/api/proto/document/{doc_id}'>"
        )

    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{name} — p.{page}</title>
  <style>
    html, body {{ margin: 0; padding: 0; height: 100%; background: #1b1f28; color: #e1e4e8; font-family: -apple-system, sans-serif; }}
    header {{ padding: 8px 16px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; }}
    header .title {{ font-size: 13px; color: #c9d1d9; }}
    header a {{ color: #58a6ff; text-decoration: none; font-size: 12px; }}
    header a:hover {{ text-decoration: underline; }}
    iframe {{ width: 100%; height: calc(100vh - 40px); border: 0; background: #fff; }}
  </style>
</head>
<body>
  <header>
    <span class="title">{name} — page {page}</span>
    <a href="/api/proto/document/{doc_id}" target="_blank">open raw ↗</a>
  </header>
  <iframe src="{src}" title="PDF"></iframe>
</body>
</html>""")


@router.get("/document/{doc_id}")
def document_file(doc_id: str):
    from db_helpers import result_to_dicts
    from proto.db_proto import proto_db
    result = proto_db.query(
        "MATCH (d:Document {id: $id}) RETURN d.path AS path, d.name AS name, d.kind AS kind",
        {"id": doc_id},
    )
    rows = result_to_dicts(result)
    if not rows:
        raise HTTPException(404, "Document not found")
    p = resolve_source(rows[0].get("path") or "")
    name = rows[0].get("name") or "document"
    kind = rows[0].get("kind")
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
def asset_image(asset_id: str):
    from proto.db_proto import proto_db
    from db_helpers import result_to_dicts
    result = proto_db.query(
        "MATCH (i:ImageAsset {id: $id}) RETURN i.path AS path, i.name AS name",
        {"id": asset_id},
    )
    rows = result_to_dicts(result)
    if not rows:
        raise HTTPException(404, "Asset not found")
    p = resolve_source(rows[0].get("path") or "")
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
def stats():
    from proto.db_proto import proto_db
    return proto_db.stats()
