"""Gramag combined server — proto KB + ERP graph on Fly.

Two FalkorDB graphs on the same instance:
- gramag_proto: multimodal KB (supplier manuals, 7781 sections)
- gramag: ERP data (6-machine demo subset — machines, service docs, parts)
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from auth_router import router as auth_router
from proto.db_proto import proto_db
from proto.router import router as proto_router
from erp_router import router as erp_router
from mission_router import router as mission_router
from fleet_router import router as fleet_router

app = FastAPI(title="Gramag Knowledge Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(proto_router)
app.include_router(erp_router)
app.include_router(mission_router)
app.include_router(fleet_router)


@app.on_event("startup")
def startup():
    try:
        proto_db.connect()
        stats = proto_db.stats()
        print(f"Proto graph connected: {sum(stats['nodes'].values())} nodes")
    except Exception as e:
        print(f"WARNING: proto graph not available: {e}")

    try:
        from db import db
        db.connect()
        erp_stats = db.stats()
        print(f"ERP graph connected: {sum(erp_stats['nodes'].values())} nodes")
    except Exception as e:
        print(f"WARNING: ERP graph not available: {e}")


@app.get("/health")
def health():
    result = {"status": "ok"}
    try:
        result["proto_graph"] = proto_db.stats()
    except Exception as e:
        result["proto_graph_error"] = str(e)
    try:
        from db import db
        result["erp_graph"] = db.stats()
    except Exception as e:
        result["erp_graph_error"] = str(e)
    if "proto_graph_error" in result and "erp_graph_error" in result:
        result["status"] = "degraded"
    return result


# ── SPA serving ───────────────────────────────────────────────────
SPA_DIR = os.path.join(os.path.dirname(__file__), "web", "dist")

if os.path.isdir(SPA_DIR):
    _assets = os.path.join(SPA_DIR, "assets")
    if os.path.isdir(_assets):
        app.mount("/einsatzplaner/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/")
    async def root():
        return RedirectResponse(url="/einsatzplaner")

    @app.get("/einsatzplaner/{rest:path}")
    @app.get("/einsatzplaner")
    async def serve_spa(rest: str = ""):
        return FileResponse(os.path.join(SPA_DIR, "index.html"))
