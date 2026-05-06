"""Gramag Knowledge Assistant — FastAPI Server (v2 with Knowledge Graph)"""
import numpy as np
import pickle, os, json
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, INDEX_DIR, CHAT_MODEL, PDF_DIR
from auth_router import router as auth_router
from mission_router import router as mission_router
from fleet_router import router as fleet_router
from proto.router import router as proto_router

client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="Gramag Knowledge Assistant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(auth_router)
app.include_router(mission_router)
app.include_router(fleet_router)
app.include_router(proto_router)

# Serve PDFs for citation links
app.mount("/api/pdfs", StaticFiles(directory=PDF_DIR), name="pdfs")

# Build filename -> relative path map for PDF links
_pdf_path_map: dict[str, str] = {}
for _root, _, _files in os.walk(PDF_DIR):
    for _f in _files:
        if _f.lower().endswith(".pdf"):
            _pdf_path_map[_f.lower()] = os.path.relpath(os.path.join(_root, _f), PDF_DIR)

# Load index on startup
emb_matrix = None
metadata = None


@app.on_event("startup")
def load():
    global emb_matrix, metadata
    print("Loading numpy index...")
    emb_matrix = np.load(os.path.join(INDEX_DIR, "embeddings_normed.npy"))
    with open(os.path.join(INDEX_DIR, "metadata.pkl"), "rb") as f:
        metadata = pickle.load(f)
    print(f"Ready: {len(metadata)} chunks")

    # Try to connect FalkorDB
    try:
        from db import db
        db.connect()
        print("FalkorDB connected")
    except Exception as e:
        print(f"FalkorDB not available: {e}")


def search(query: str, top_k: int = 12):
    r = client.models.embed_content(
        model="gemini-embedding-001",
        contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    qv = np.array(r.embeddings[0].values, dtype=np.float32)
    qv /= np.linalg.norm(qv)
    scores = emb_matrix @ qv
    top_idx = np.argsort(scores)[-top_k:][::-1]
    return [(float(scores[i]), metadata[i]) for i in top_idx]


class Question(BaseModel):
    query: str


# ── V1 endpoints (kept as-is) ────────────────────────────────────────

@app.post("/api/ask")
def ask(q: Question):
    results = search(q.query)

    ctx_parts = []
    sources = []
    for i, (score, chunk) in enumerate(results):
        src = chunk.get("source", "?")
        ctype = chunk.get("type", "?")
        supplier = chunk.get("supplier", "")
        text = chunk["text"][:600]
        label = f"[{i+1}] ({ctype}) {src}"
        if supplier:
            label += f" [{supplier}]"
        ctx_parts.append(f"{label}\n{text}")
        sources.append({"rank": i+1, "source": src, "type": ctype, "score": round(score, 3), "text": chunk["text"][:200]})

    ctx = "\n\n---\n\n".join(ctx_parts)

    resp = client.models.generate_content(
        model=CHAT_MODEL,
        contents=[{"role": "user", "parts": [{"text": f"""You are a technical assistant for Gramag Grafische Maschinen AG, a Swiss company servicing printing, folding, cutting, enveloping, and labelling machines.

RULES:
- Answer ONLY from context. Do not invent.
- Reference sources [1],[2] etc.
- Same language as question.
- Be concise, practical — for service technicians.
- Include part numbers when found.

CONTEXT:
{ctx}

QUESTION: {q.query}"""}]}],
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=2000),
    )

    return {"answer": resp.text, "sources": sources}


@app.get("/api/search")
def api_search(q: str, top_k: int = 10):
    results = search(q, top_k)
    return [{"score": round(s, 3), "source": m.get("source", "?"), "type": m.get("type", "?"), "text": m["text"][:300]} for s, m in results]


@app.get("/api/stats")
def stats():
    type_counts = {}
    for c in metadata:
        t = c.get("type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    return {"total_chunks": len(metadata), "by_type": type_counts}


# ── V2 endpoints (hybrid with Knowledge Graph) ───────────────────────

@app.post("/api/ask/v2")
def ask_v2(q: Question):
    """Hybrid retrieval: graph traversal + vector search."""
    from retriever import retrieve, detect_intent

    results = retrieve(q.query, top_k=12)
    intent = detect_intent(q.query)

    # Build context — separate graph context from vector context
    graph_parts = []
    vector_parts = []
    sources = []

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
        if method == "graph_traversal":
            graph_parts.append(entry)
        else:
            vector_parts.append(entry)

        sources.append({
            "rank": i + 1,
            "source": src,
            "type": ctype,
            "method": method,
            "score": round(r.get("score", 0), 3),
            "text": r.get("text", "")[:200],
        })

    # Build enhanced prompt with graph section
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
- When graph data provides structured info (service history, machine ownership, related parts), present it clearly.

GRAPH CONTEXT (structured ERP data — service history, machine relationships, parts):
{graph_ctx}

DOCUMENT CONTEXT (technical manuals, ERP text):
{vector_ctx}

DETECTED INTENT: {json.dumps(intent)}

QUESTION: {q.query}"""

    resp = client.models.generate_content(
        model=CHAT_MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=2000),
    )

    graph_vector_count = sum(1 for s in sources if s["method"] == "graph_vector")
    return {
        "answer": resp.text,
        "sources": sources,
        "intent": intent,
        "graph_results": len(graph_parts) + graph_vector_count,
        "vector_results": len(vector_parts) - graph_vector_count,
    }


@app.get("/api/machine/{erp_id}")
def get_machine(erp_id: str):
    """Full machine context from knowledge graph."""
    try:
        from db import db
        from db_helpers import result_to_dicts

        result = db.query("""
            MATCH (m:Machine {erp_id: $erp_id})
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            OPTIONAL MATCH (m)-[:IS_TYPE]->(mt:MachineType)
            OPTIONAL MATCH (m)-[:MADE_BY]->(mb:MachineBrand)
            OPTIONAL MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
            OPTIONAL MATCH (sj)-[:USED_PART]->(p:Part)
            WHERE NOT p.noise
            WITH m, c, mt, mb,
                 collect(DISTINCT {title: sj.title, date: sj.date, nummer: sj.nummer})[0..10] AS jobs,
                 collect(DISTINCT {titel: p.titel, nummer: p.nummer})[0..20] AS parts
            RETURN m.title AS title, m.serial_number AS serial,
                   m.erp_id AS erp_id,
                   c.name AS customer, c.city AS city,
                   mt.name AS machine_type, mb.name AS brand,
                   jobs, parts
        """, {"erp_id": erp_id})

        row = result_to_dicts(result)
        if not row:
            return {"error": "Machine not found"}
        return row[0]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/part/{nummer}")
def get_part(nummer: str):
    """Part context + usage history from knowledge graph."""
    try:
        from db import db
        from db_helpers import result_to_dicts

        result = db.query("""
            MATCH (p:Part {nummer: $nummer})
            OPTIONAL MATCH (sj:ServiceJob)-[u:USED_PART]->(p)
            OPTIONAL MATCH (sj)-[:FOR_MACHINE]->(m:Machine)
            OPTIONAL MATCH (sj)-[:FOR_CUSTOMER]->(c:Customer)
            WITH p,
                 collect(DISTINCT {machine: m.title, machine_erp_id: m.erp_id, customer: c.name, job: sj.title, date: sj.date})[0..15] AS usage,
                 count(sj) AS usage_count
            OPTIONAL MATCH (p)-[:MENTIONED_IN]->(ms:ManualSection)
            WITH p, usage_count, usage,
                 collect(DISTINCT ms.title)[0..5] AS manual_refs
            OPTIONAL MATCH (p)-[:OFTEN_USED_WITH]-(p2:Part)
            WITH p, usage_count, usage, manual_refs,
                 collect(DISTINCT {titel: p2.titel, nummer: p2.nummer}) AS raw_co
            RETURN p.titel AS titel, p.nummer AS nummer,
                   p.manufacturer_nr AS manufacturer_nr, p.noise AS noise,
                   usage_count, usage, manual_refs,
                   [x IN raw_co WHERE x.nummer IS NOT NULL][0..10] AS co_parts
        """, {"nummer": nummer})

        row = result_to_dicts(result)
        if not row:
            return {"error": "Part not found"}
        return row[0]
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/graph/stats")
def graph_stats():
    """Node and relationship counts from the knowledge graph."""
    try:
        from db import db
        return db.stats()
    except Exception as e:
        return {"error": str(e)}


# ── UI ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def ui():
    return """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gramag Knowledge Assistant</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0f1117; color: #e1e4e8; height: 100vh; display: flex; flex-direction: column; }
  .header { padding: 16px 24px; border-bottom: 1px solid #21262d; display: flex; align-items: center; gap: 12px; background: #161b22; }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header .badge { font-size: 11px; background: #238636; color: #fff; padding: 2px 8px; border-radius: 10px; }
  .header .badge.graph { background: #8957e5; }
  .chat { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
  .msg { max-width: 85%; padding: 12px 16px; border-radius: 12px; line-height: 1.6; font-size: 14px; }
  .msg.user { align-self: flex-end; background: #1f6feb; color: #fff; border-bottom-right-radius: 4px; }
  .msg.bot { align-self: flex-start; background: #21262d; border-bottom-left-radius: 4px; }
  .msg.bot .answer { white-space: pre-wrap; }
  .msg.bot .answer strong, .msg.bot .answer b { color: #58a6ff; }
  .msg.bot .answer ul, .msg.bot .answer ol { padding-left: 20px; margin: 6px 0; }
  .msg.bot .answer li { margin: 3px 0; }
  .retrieval-info { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .retrieval-badge { font-size: 11px; padding: 3px 10px; border-radius: 12px; font-weight: 600; }
  .retrieval-badge.graph-badge { background: #8957e5; color: #fff; }
  .retrieval-badge.vector-badge { background: #238636; color: #fff; }
  .source-panels { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
  .source-panel { border-radius: 8px; padding: 10px 14px; font-size: 12px; }
  .source-panel.graph-panel { background: #1a1232; border: 1px solid #8957e5; }
  .source-panel.vector-panel { background: #161b22; border: 1px solid #30363d; }
  .source-panel .panel-header { font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; cursor: pointer; }
  .source-panel .panel-header .icon { font-size: 14px; }
  .source-panel.graph-panel .panel-header { color: #d2a8ff; }
  .source-panel.vector-panel .panel-header { color: #7ee787; }
  .source-panel .source-list { display: flex; flex-direction: column; gap: 4px; }
  .source-item { padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
  .source-item:last-child { border-bottom: none; }
  .source-item .src-name { color: #c9d1d9; }
  .source-item .src-score { color: #8b949e; font-size: 11px; white-space: nowrap; }
  .source-item .src-preview { color: #8b949e; font-size: 11px; margin-top: 2px; max-height: 0; overflow: hidden; transition: max-height 0.3s; }
  .source-item:hover .src-preview { max-height: 60px; }
  .graph-context-detail { margin-top: 6px; padding: 8px 10px; background: rgba(137,87,229,0.1); border-radius: 6px; font-size: 12px; color: #c9d1d9; white-space: pre-wrap; line-height: 1.5; max-height: 200px; overflow-y: auto; }
  .input-area { padding: 16px 24px; border-top: 1px solid #21262d; background: #161b22; }
  .input-row { display: flex; gap: 10px; max-width: 900px; margin: 0 auto; }
  .input-row input { flex: 1; background: #0d1117; border: 1px solid #30363d; color: #e1e4e8; padding: 12px 16px; border-radius: 8px; font-size: 14px; outline: none; }
  .input-row input:focus { border-color: #1f6feb; }
  .input-row button { background: #238636; color: #fff; border: none; padding: 12px 24px; border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 500; }
  .input-row button:hover { background: #2ea043; }
  .input-row button:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
  .loading { display: inline-block; }
  .loading::after { content: ''; animation: dots 1.5s infinite; }
  @keyframes dots { 0% { content: '.'; } 33% { content: '..'; } 66% { content: '...'; } }
  .examples { text-align: center; color: #8b949e; margin-top: 40px; }
  .examples h3 { margin-bottom: 12px; font-weight: 500; }
  .examples button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 8px 16px; border-radius: 8px; margin: 4px; cursor: pointer; font-size: 13px; }
  .examples button:hover { border-color: #1f6feb; color: #58a6ff; }
  .stats { font-size: 12px; color: #484f58; }
  .toggle { display: flex; gap: 4px; margin-left: auto; }
  .toggle button { background: #21262d; border: 1px solid #30363d; color: #8b949e; padding: 4px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  .toggle button.active { background: #238636; color: #fff; border-color: #238636; }
  .toggle button.active.graph-active { background: #8957e5; border-color: #8957e5; }
</style>
</head>
<body>
<div class="header">
  <h1>Gramag Knowledge Assistant</h1>
  <span class="badge">v2</span>
  <span class="badge graph">Graph</span>
  <span class="stats" id="stats"></span>
  <div class="toggle">
    <button id="btnV1" onclick="setVersion('v1')">v1</button>
    <button id="btnV2" class="active graph-active" onclick="setVersion('v2')">v2 Graph</button>
  </div>
</div>
<div class="chat" id="chat">
  <div class="examples" id="examples">
    <h3>Beispiel-Fragen</h3>
    <button onclick="ask(this.textContent)">Welche Ersatzteile braucht eine Falzmaschine K800?</button>
    <button onclick="ask(this.textContent)">How to clean the printhead on Avery 64-xx?</button>
    <button onclick="ask(this.textContent)">Baumer hhs XTS2 Störung — was tun?</button>
    <button onclick="ask(this.textContent)">CMC 250 Kuvertieranlage Teile</button>
    <button onclick="ask(this.textContent)">Welche Maschinen hat Gramag im Service?</button>
  </div>
</div>
<div class="input-area">
  <div class="input-row">
    <input type="text" id="input" placeholder="Frage eingeben..." autofocus />
    <button id="btn" onclick="ask()">Fragen</button>
  </div>
</div>
<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const btn = document.getElementById('btn');
let apiVersion = 'v2';

function setVersion(v) {
  apiVersion = v;
  document.getElementById('btnV1').className = v === 'v1' ? 'active' : '';
  document.getElementById('btnV2').className = v === 'v2' ? 'active graph-active' : '';
}

Promise.all([
  fetch('/api/stats').then(r=>r.json()),
  fetch('/api/graph/stats').then(r=>r.json()).catch(()=>null)
]).then(([vecStats, graphStats]) => {
  let text = Object.entries(vecStats.by_type).map(([k,v])=>`${k}: ${v}`).join(' | ') + ` | total: ${vecStats.total_chunks}`;
  if (graphStats && graphStats.nodes) {
    const gn = Object.values(graphStats.nodes).reduce((a,b)=>a+b, 0);
    const gr = Object.values(graphStats.relationships).reduce((a,b)=>a+b, 0);
    text += ` | graph: ${gn} nodes, ${gr} rels`;
  }
  document.getElementById('stats').textContent = text;
});

input.addEventListener('keydown', e => { if(e.key==='Enter' && !btn.disabled) ask(); });

async function ask(text) {
  const q = text || input.value.trim();
  if (!q) return;
  input.value = '';

  const ex = document.getElementById('examples');
  if (ex) ex.remove();

  chat.innerHTML += `<div class="msg user">${esc(q)}</div>`;
  const botMsg = document.createElement('div');
  botMsg.className = 'msg bot';
  botMsg.innerHTML = '<div class="answer"><span class="loading">Thinking</span></div>';
  chat.appendChild(botMsg);
  chat.scrollTop = chat.scrollHeight;

  btn.disabled = true;
  try {
    const endpoint = apiVersion === 'v2' ? '/api/ask/v2' : '/api/ask';
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: q})
    });
    const data = await resp.json();

    let html = '<div class="answer">' + md(data.answer) + '</div>';

    // Retrieval info badges
    const gRes = data.graph_results || 0;
    const vRes = data.vector_results || 0;
    html += '<div class="retrieval-info">';
    if (gRes > 0) html += `<span class="retrieval-badge graph-badge">Graph: ${gRes} Quellen</span>`;
    if (vRes > 0) html += `<span class="retrieval-badge vector-badge">Vector: ${vRes} Quellen</span>`;
    html += '</div>';

    // Split sources by method
    const graphSources = (data.sources||[]).filter(s => s.method === 'graph_traversal' || s.method === 'graph_vector');
    const vectorSources = (data.sources||[]).filter(s => s.method !== 'graph_traversal' && s.method !== 'graph_vector');

    html += '<div class="source-panels">';

    // Graph panel
    if (graphSources.length > 0) {
      html += '<div class="source-panel graph-panel">';
      html += '<div class="panel-header"><span class="icon">&#9653;</span> Graph-Quellen (FalkorDB Knowledge Graph)</div>';
      html += '<div class="source-list">';
      graphSources.forEach(s => {
        const methodLabel = s.method === 'graph_traversal' ? 'Traversal' : 'Vector';
        html += `<div class="source-item"><div><span class="src-name">[${s.rank}] ${esc(s.source)}</span> <span class="src-score">${methodLabel} &middot; ${s.score}</span>`;
        if (s.text) html += `<div class="src-preview">${esc(s.text.substring(0,150))}</div>`;
        html += '</div></div>';
      });
      html += '</div>';

      // Show graph traversal detail (first traversal source text)
      const traversalSrc = graphSources.find(s => s.method === 'graph_traversal');
      if (traversalSrc && traversalSrc.text) {
        html += `<div class="graph-context-detail">${esc(traversalSrc.text)}</div>`;
      }

      html += '</div>';
    }

    // Vector panel (collapsed by default)
    if (vectorSources.length > 0) {
      html += '<div class="source-panel vector-panel">';
      html += `<div class="panel-header" onclick="this.parentElement.classList.toggle('expanded')"><span class="icon">&#9653;</span> Dokument-Quellen (${vectorSources.length})</div>`;
      html += '<div class="source-list">';
      vectorSources.slice(0,7).forEach(s => {
        html += `<div class="source-item"><div><span class="src-name">[${s.rank}] ${esc(s.source)}</span> <span class="src-score">${s.score}</span></div></div>`;
      });
      html += '</div></div>';
    }

    html += '</div>';

    botMsg.innerHTML = html;
  } catch(e) {
    botMsg.innerHTML = `<div class="answer" style="color:#f85149">Error: ${e.message}</div>`;
  }
  btn.disabled = false;
  chat.scrollTop = chat.scrollHeight;
  input.focus();
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function md(s) {
  return s
    .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
    .replace(/\\*(.+?)\\*/g, '<em>$1</em>')
    .replace(/^(\\d+)\\.\\s/gm, '<br>$1. ')
    .replace(/^[\\-\\*]\\s+(.+)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\\/li>)/gs, '<ul>$1</ul>')
    .replace(/\\n\\n/g, '<br><br>')
    .replace(/\\n/g, '<br>');
}
</script>
</body>
</html>"""


# ── Einsatzplaner SPA serving ─────────────────────────────────────

SPA_DIR = os.path.join(os.path.dirname(__file__), "web", "dist")

if os.path.isdir(SPA_DIR):
    _assets_dir = os.path.join(SPA_DIR, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/einsatzplaner/assets", StaticFiles(directory=_assets_dir), name="spa-assets")

    @app.get("/einsatzplaner/{rest:path}")
    @app.get("/einsatzplaner")
    async def serve_spa(rest: str = ""):
        return FileResponse(os.path.join(SPA_DIR, "index.html"))
