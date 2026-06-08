"""Multimodal answer generation using Azure OpenAI vision-enabled chat."""

from pathlib import Path

from ai_client import image_content, vision_chat
from config import AZURE_OPENAI_VISION_DEPLOYMENT
from proto import resolve_cache
from proto.retriever import retrieve

ANSWER_MODEL = AZURE_OPENAI_VISION_DEPLOYMENT
DEEP_MODEL = AZURE_OPENAI_VISION_DEPLOYMENT

SYSTEM_PROMPT = """\
You are a service assistant for Gramag Grafische Maschinen AG technicians.

You are given evidence for a user question. Each evidence block is labelled
[1], [2], ... — it contains a header line (machine / document / page), the
extracted text, the vision analysis of that page, and an image of that page.
Images follow the text block they belong to — the caption "(image for [n])"
marks which evidence item an image corresponds to.

TREAT THE SUPPLIED EVIDENCE AS COMPLETE AND AUTHORITATIVE. Every numbered
block was deliberately retrieved to answer the question — do not claim it is
missing or that you only see "other pages". If a page is labelled [n] as
page N in the header, that IS page N. Do not infer content from a Table of
Contents if the actual content is provided.

Cite inline as [CITE: <machine> / <doc_name> / page N] or [CITE: <machine> /
<config_name>]. Answer in the language of the question (German/English/Polish).
Be concise but include concrete part numbers, order numbers, positions, and
spatial descriptions visible on the supplied images.

If — after reading all the evidence — the specific information is genuinely
absent, say so; but never fabricate limitations that are not real.
"""


def _build_evidence_parts(hits: list[dict], max_images: int = 6) -> tuple[list, list[dict]]:
    parts: list = []
    citations: list[dict] = []
    img_count = 0

    for i, h in enumerate(hits, start=1):
        label = h["label"]
        machine = h.get("machine_folder") or "?"
        doc = h.get("doc_name") or "?"

        if label == "ManualSection":
            cite = f"[{i}] {machine} / {doc} / page {h.get('page')}"
            text_block = f"=== EVIDENCE {cite} ===\nTEXT EXTRACT:\n{h.get('text') or '(no extractable text)'}\n\nVISION ANALYSIS:\n{h.get('vision_desc') or ''}"
            parts.append({"type": "text", "text": text_block})
            png = resolve_cache(h.get("png_path") or "")
            if png and img_count < max_images and Path(png).exists():
                parts.append({"type": "text", "text": f"(image for [{i}] — page {h.get('page')} of {doc})"})
                parts.append(image_content(png, mime_type="image/png"))
                img_count += 1
            citations.append({
                "idx": i, "kind": "page", "machine": machine, "doc": doc,
                "page": h.get("page"), "section_id": h.get("id"),
                "score": h.get("score"),
            })
        elif label == "ConfigFile":
            cite = f"[{i}] {machine} / config: {h.get('name')}"
            body = f"{cite}\nSUMMARY: {h.get('summary') or ''}\n\nCONTENT (truncated):\n{(h.get('content') or '')[:2000]}"
            parts.append({"type": "text", "text": body})
            citations.append({
                "idx": i, "kind": "config", "machine": machine, "doc": doc,
                "name": h.get("name"), "score": h.get("score"),
            })
        elif label == "ImageAsset":
            cite = f"[{i}] {machine} / image: {h.get('name')}"
            parts.append({"type": "text", "text": f"{cite}\nCAPTION: {h.get('caption') or ''}"})
            citations.append({
                "idx": i, "kind": "image", "machine": machine, "doc": doc,
                "name": h.get("name"), "score": h.get("score"),
            })
    return parts, citations


def answer(query: str, *, machine_slug: str | None = None, customer: str | None = None,
           top_k: int = 6, deep: bool = False) -> dict:
    hits = retrieve(query, top_k=top_k, machine_slug=machine_slug, customer=customer)
    if not hits:
        return {
            "answer": "Brak wyników w bazie dla tego zapytania.",
            "citations": [], "hits": [],
        }

    evidence_parts, citations = _build_evidence_parts(hits)
    user_parts = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": f"\n\nQUESTION: {query}\n\nEVIDENCE:\n"},
        *evidence_parts,
        {"type": "text", "text": "\n\nAnswer now, citing sources as [n] inline."},
    ]

    model = DEEP_MODEL if deep else ANSWER_MODEL
    answer_text = vision_chat(user_parts, deployment=model, temperature=0.1, max_tokens=2000)
    return {
        "answer": answer_text or "",
        "citations": citations,
        "hits": [
            {k: v for k, v in h.items() if k not in ("merged",)}
            for h in hits
        ],
        "model": model,
    }
