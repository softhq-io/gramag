"""Deprecated Gemini multimodal demo.

Runtime code now uses Azure OpenAI via ai_client.py.

Interactive demo: multimodal knowledge base retrieval + generation.
Serves a web UI on http://localhost:8001

Uses cached indices from test_multimodal_embed.py.
"""

import base64
import json
import os
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Config ──────────────────────────────────────────────────────────────
CACHE_DIR = Path(__file__).parent / "test_embed_cache"
TEXT_MODEL = "gemini-embedding-001"
IMAGE_MODEL = "gemini-embedding-2-preview"
GEN_MODEL = "gemini-3-flash-preview"
DIM = 768

client = genai.Client(api_key=GEMINI_API_KEY)

# ── Load indices ────────────────────────────────────────────────────────
text_index = np.load(CACHE_DIR / "text_index.npy")
image_index = np.load(CACHE_DIR / "image_index.npy")
with open(CACHE_DIR / "pages.json") as f:
    pages = json.load(f)

print(f"Loaded {len(pages)} pages, text_index={text_index.shape}, image_index={image_index.shape}")

# ── Helpers ──────────────────────────────────────────────────────────────
def embed_query_text(query: str) -> np.ndarray:
    result = client.models.embed_content(
        model=TEXT_MODEL, contents=query,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=DIM),
    )
    return np.array(result.embeddings[0].values, dtype=np.float32)


def embed_query_image(query: str) -> np.ndarray:
    result = client.models.embed_content(
        model=IMAGE_MODEL, contents=query,
        config=types.EmbedContentConfig(output_dimensionality=DIM),
    )
    return np.array(result.embeddings[0].values, dtype=np.float32)


def cosine_search(query_vec: np.ndarray, index: np.ndarray, top_k: int = 5):
    qn = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(index, axis=1, keepdims=True) + 1e-10
    scores = (index / norms) @ qn
    top_idx = np.argsort(scores)[::-1][:top_k]
    return [(int(i), float(scores[i])) for i in top_idx]


def page_to_b64(page_idx: int) -> str:
    img_path = pages[page_idx]["img_path"]
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


SYSTEM_PROMPT = """You are a technical service assistant for industrial printing machines (DPM/PEM by Avery Dennison).
You answer questions from service technicians based on the provided manual pages.

Rules:
- Answer based ONLY on the provided pages. If the answer isn't visible, say so.
- Reference specific page numbers and figure numbers when possible.
- If the answer involves a diagram or photo, describe what is shown and where to look.
- Be concise and practical — technicians need quick answers.
- Answer in the same language as the question.
- Use markdown formatting for structure."""


def generate_answer(query: str, page_indices: list[int]) -> str:
    parts = []
    for idx in page_indices:
        with open(pages[idx]["img_path"], "rb") as f:
            img_bytes = f.read()
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        parts.append(types.Part(text=f"[Above: Manual page {pages[idx]['page_num']}]"))
    parts.append(types.Part(text=f"\nQuestion: {query}"))

    response = client.models.generate_content(
        model=GEN_MODEL,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, temperature=0.2, max_output_tokens=800,
        ),
    )
    return response.text


# ── FastAPI ──────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/pages", StaticFiles(directory=str(CACHE_DIR / "page_images")), name="pages")


class AskRequest(BaseModel):
    query: str
    mode: str = "hybrid"  # "text", "image", "hybrid"


@app.post("/api/ask")
async def ask(req: AskRequest):
    t0 = time.time()

    q_text = embed_query_text(req.query)
    q_image = embed_query_image(req.query)
    t_embed = time.time() - t0

    text_results = cosine_search(q_text, text_index, top_k=3)
    image_results = cosine_search(q_image, image_index, top_k=3)

    if req.mode == "text":
        final_indices = [idx for idx, _ in text_results[:3]]
    elif req.mode == "image":
        final_indices = [idx for idx, _ in image_results[:3]]
    else:
        seen = set()
        final_indices = []
        for idx, _ in text_results + image_results:
            if idx not in seen:
                seen.add(idx)
                final_indices.append(idx)
        final_indices = final_indices[:4]

    t1 = time.time()
    answer = generate_answer(req.query, final_indices)
    t_gen = time.time() - t1

    retrieved = []
    for idx in final_indices:
        p = pages[idx]
        in_text = any(i == idx for i, _ in text_results[:3])
        in_image = any(i == idx for i, _ in image_results[:3])
        t_score = next((s for i, s in text_results if i == idx), 0)
        i_score = next((s for i, s in image_results if i == idx), 0)
        retrieved.append({
            "page_num": p["page_num"],
            "img_file": f"page_{p['page_num']:03d}.png",
            "text_score": round(t_score, 3),
            "image_score": round(i_score, 3),
            "source": ("both" if in_text and in_image else "text" if in_text else "image"),
        })

    return JSONResponse({
        "answer": answer,
        "retrieved": retrieved,
        "timing": {"embed_ms": round(t_embed * 1000), "gen_ms": round(t_gen * 1000)},
        "text_top3": [{"page": pages[i]["page_num"], "score": round(s, 3)} for i, s in text_results],
        "image_top3": [{"page": pages[i]["page_num"], "score": round(s, 3)} for i, s in image_results],
    })


@app.get("/")
async def index():
    return HTMLResponse(HTML)


# ── Frontend ─────────────────────────────────────────────────────────────
HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gramag Multimodal KB Demo</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root {
    --bg: #0a0a0f; --surface: #14141f; --surface2: #1e1e2e;
    --border: #2a2a3e; --text: #e0e0e8; --text2: #8888a0;
    --accent: #6c5ce7; --accent2: #a29bfe; --green: #00b894;
    --orange: #fdcb6e; --blue: #74b9ff; --red: #ff7675;
    --radius: 12px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg); color: var(--text);
    min-height: 100vh; display: flex; flex-direction: column;
  }
  header {
    padding: 20px 32px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px;
    background: var(--surface);
  }
  header h1 { font-size: 18px; font-weight: 600; }
  header .tag {
    font-size: 11px; padding: 3px 10px; border-radius: 20px;
    background: var(--accent); color: white; font-weight: 500;
  }
  header .subtitle { font-size: 13px; color: var(--text2); margin-left: auto; }

  .container { flex: 1; display: flex; flex-direction: column; max-width: 1200px; width: 100%; margin: 0 auto; padding: 24px; gap: 20px; }

  .input-area {
    display: flex; gap: 12px; align-items: stretch;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 8px;
  }
  .input-area input {
    flex: 1; background: transparent; border: none; color: var(--text);
    font-size: 15px; padding: 12px 16px; outline: none;
  }
  .input-area input::placeholder { color: var(--text2); }
  .mode-select {
    background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); border-radius: 8px; padding: 0 12px;
    font-size: 13px; cursor: pointer; outline: none;
  }
  .input-area button {
    background: var(--accent); color: white; border: none;
    border-radius: 8px; padding: 12px 24px; font-size: 14px;
    font-weight: 600; cursor: pointer; transition: all 0.2s;
  }
  .input-area button:hover { background: var(--accent2); }
  .input-area button:disabled { opacity: 0.5; cursor: not-allowed; }

  .examples { display: flex; gap: 8px; flex-wrap: wrap; }
  .examples button {
    background: var(--surface); border: 1px solid var(--border);
    color: var(--text2); border-radius: 20px; padding: 6px 14px;
    font-size: 12px; cursor: pointer; transition: all 0.2s;
  }
  .examples button:hover { border-color: var(--accent); color: var(--text); }

  .results { display: flex; flex-direction: column; gap: 20px; }

  .answer-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 24px; display: none;
  }
  .answer-card.visible { display: block; }
  .answer-card .label {
    font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
    color: var(--accent2); margin-bottom: 12px; font-weight: 600;
  }
  .answer-card .content { font-size: 14px; line-height: 1.7; }
  .answer-card .content h1,.answer-card .content h2,.answer-card .content h3 {
    font-size: 15px; margin: 12px 0 6px; color: var(--accent2);
  }
  .answer-card .content ul { padding-left: 20px; }
  .answer-card .content li { margin: 4px 0; }
  .answer-card .content strong { color: var(--green); }
  .answer-card .content code { background: var(--surface2); padding: 1px 6px; border-radius: 4px; font-size: 13px; }

  .timing {
    display: flex; gap: 16px; margin-top: 16px; padding-top: 12px;
    border-top: 1px solid var(--border);
  }
  .timing span { font-size: 11px; color: var(--text2); }
  .timing .val { color: var(--green); font-weight: 600; }

  .pages-row {
    display: flex; gap: 12px; overflow-x: auto; padding-bottom: 8px;
  }
  .page-card {
    flex: 0 0 auto; width: 220px; background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--radius);
    overflow: hidden; cursor: pointer; transition: all 0.2s;
  }
  .page-card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .page-card img { width: 100%; aspect-ratio: 0.707; object-fit: cover; background: white; }
  .page-card .meta {
    padding: 10px 12px; display: flex; justify-content: space-between;
    align-items: center;
  }
  .page-card .pnum { font-size: 13px; font-weight: 600; }
  .page-card .scores { display: flex; gap: 6px; }
  .page-card .scores .badge {
    font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: 600;
  }
  .badge.text { background: #2d3436; color: var(--blue); }
  .badge.image { background: #2d3436; color: var(--orange); }
  .badge.both { background: #2d3436; color: var(--green); }

  .retrieval-detail {
    display: flex; gap: 24px; background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 20px; font-size: 12px; display: none;
  }
  .retrieval-detail.visible { display: flex; }
  .retrieval-detail .col { flex: 1; }
  .retrieval-detail .col-title {
    font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
    color: var(--text2); margin-bottom: 8px; font-weight: 600;
  }
  .retrieval-detail .row { display: flex; gap: 8px; margin: 4px 0; align-items: center; }
  .retrieval-detail .row .pn { font-weight: 600; width: 36px; }
  .retrieval-detail .score-bar {
    height: 6px; border-radius: 3px; transition: width 0.5s;
  }
  .retrieval-detail .score-val { color: var(--text2); width: 40px; text-align: right; }

  .lightbox {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.92);
    z-index: 100; justify-content: center; align-items: center; cursor: zoom-out;
  }
  .lightbox.visible { display: flex; }
  .lightbox img { max-width: 90vw; max-height: 90vh; border-radius: 8px; box-shadow: 0 0 60px rgba(0,0,0,0.5); }

  .spinner { display: none; text-align: center; padding: 40px; }
  .spinner.visible { display: block; }
  .spinner::after {
    content: ''; display: inline-block; width: 32px; height: 32px;
    border: 3px solid var(--border); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <h1>Gramag Knowledge Base</h1>
  <span class="tag">Multimodal RAG</span>
  <span class="subtitle">DPM/PEM Operating Instructions &mdash; gemini-embedding-2 + Gemini 3 Flash</span>
</header>

<div class="container">
  <div class="input-area">
    <input id="query" type="text" placeholder="Ask about the DPM/PEM printer..." autofocus />
    <select id="mode" class="mode-select">
      <option value="hybrid" selected>Hybrid</option>
      <option value="text">Text only</option>
      <option value="image">Image only</option>
    </select>
    <button id="askBtn" onclick="ask()">Ask</button>
  </div>

  <div class="examples">
    <button onclick="setQ('Where is the RS232 port on the DPM?')">RS232 port location</button>
    <button onclick="setQ('How to insert a CF memory card?')">Insert CF card</button>
    <button onclick="setQ('How to carry the printer safely?')">Carry printer safely</button>
    <button onclick="setQ('What does the control panel display show?')">Control panel</button>
    <button onclick="setQ('Wie wechsle ich das Farbband?')">Farbband wechseln</button>
    <button onclick="setQ('Wo befindet sich der Druckkopf?')">Druckkopf Position</button>
    <button onclick="setQ('What is the pinch point warning?')">Pinch point warning</button>
    <button onclick="setQ('Jak podłączyć kabel zasilający?')">Kabel zasilający</button>
  </div>

  <div class="spinner" id="spinner"></div>

  <div class="results">
    <div class="retrieval-detail" id="retrieval">
      <div class="col">
        <div class="col-title" style="color:var(--blue)">Text retrieval</div>
        <div id="textResults"></div>
      </div>
      <div class="col">
        <div class="col-title" style="color:var(--orange)">Image retrieval</div>
        <div id="imageResults"></div>
      </div>
    </div>

    <div class="pages-row" id="pagesRow"></div>

    <div class="answer-card" id="answerCard">
      <div class="label">Answer <span id="answerMode"></span></div>
      <div class="content" id="answerContent"></div>
      <div class="timing" id="timing"></div>
    </div>
  </div>
</div>

<div class="lightbox" id="lightbox" onclick="this.classList.remove('visible')">
  <img id="lightboxImg" />
</div>

<script>
const $ = id => document.getElementById(id);

function setQ(q) { $('query').value = q; ask(); }

$('query').addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });

async function ask() {
  const query = $('query').value.trim();
  if (!query) return;
  const mode = $('mode').value;

  $('askBtn').disabled = true;
  $('spinner').classList.add('visible');
  $('answerCard').classList.remove('visible');
  $('retrieval').classList.remove('visible');
  $('pagesRow').innerHTML = '';

  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query, mode})
    });
    const data = await res.json();

    // Retrieval bars
    renderRetrievalBars('textResults', data.text_top3, 'var(--blue)');
    renderRetrievalBars('imageResults', data.image_top3, 'var(--orange)');
    $('retrieval').classList.add('visible');

    // Page cards
    $('pagesRow').innerHTML = data.retrieved.map(p => `
      <div class="page-card" onclick="showPage('${p.img_file}')">
        <img src="/pages/${p.img_file}" loading="lazy" />
        <div class="meta">
          <span class="pnum">Page ${p.page_num}</span>
          <div class="scores">
            <span class="badge ${p.source}">${p.source}</span>
            ${p.text_score ? `<span class="badge text">T:${p.text_score}</span>` : ''}
            ${p.image_score ? `<span class="badge image">I:${p.image_score}</span>` : ''}
          </div>
        </div>
      </div>
    `).join('');

    // Answer
    $('answerMode').textContent = `(${mode} retrieval)`;
    $('answerContent').innerHTML = marked.parse(data.answer);
    $('timing').innerHTML = `
      <span>Embedding: <span class="val">${data.timing.embed_ms}ms</span></span>
      <span>Generation: <span class="val">${data.timing.gen_ms}ms</span></span>
      <span>Total: <span class="val">${data.timing.embed_ms + data.timing.gen_ms}ms</span></span>
    `;
    $('answerCard').classList.add('visible');

  } catch(e) {
    $('answerContent').innerHTML = `<span style="color:var(--red)">Error: ${e.message}</span>`;
    $('answerCard').classList.add('visible');
  } finally {
    $('askBtn').disabled = false;
    $('spinner').classList.remove('visible');
  }
}

function renderRetrievalBars(containerId, results, color) {
  const max = Math.max(...results.map(r => r.score));
  $(containerId).innerHTML = results.map(r => `
    <div class="row">
      <span class="pn">p.${r.page}</span>
      <div style="flex:1; background: var(--surface2); border-radius: 3px; height: 6px;">
        <div class="score-bar" style="width:${(r.score/max*100).toFixed(0)}%; background:${color};"></div>
      </div>
      <span class="score-val">${r.score}</span>
    </div>
  `).join('');
}

function showPage(file) {
  $('lightboxImg').src = '/pages/' + file;
  $('lightbox').classList.add('visible');
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
