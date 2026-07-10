"""Multimodal answer generation using Azure OpenAI vision-enabled chat."""

from pathlib import Path

from ai_client import image_content, vision_chat, vision_chat_messages
from config import AZURE_OPENAI_VISION_DEPLOYMENT
from proto import resolve_cache
from proto.erp_context import format_erp_context, retrieve_erp_context
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

ERP_ANSWER_RULES = """\
When ERP context is available:
- Start with the practical conclusion, then give only the most relevant
  recent service entries, recurring issues, and frequent parts.
- For grouped ERP records, mention the group identifier once and treat the
  records as one related machine/line. Do not list every ERP ID or every
  title unless the user explicitly asks for the raw mapping.
- Summarize messy service comments before quoting them. Keep raw comments
  short and only include them when they materially answer the question.
- Use natural source labels such as "(ERP service history)" or
  "(ERP part usage)"; do not emit repeated bracket citations like
  "[CITE: ERP service history]".
- Do not offer to create/send PDFs, request offers, contact suppliers, open
  tickets, or perform external actions unless such an action tool is actually
  available and has been used. You may suggest the user can request/export
  more detail separately.
"""

CHAT_SYSTEM_PROMPT = SYSTEM_PROMPT + """

You are answering inside a persistent specialist chat. Use context in this
priority order:
1. The current chat transcript, especially the latest user message.
2. Relevant previous specialist chat memory for the same machine/customer.
3. Retrieved documentation evidence.
4. Linked ERP machine context, when available, for service history, parts,
   customer, and machine identity.

Authenticated specialist chat memory is trusted operational knowledge. If it
conflicts with documentation evidence, prefer the specialist memory and mention
the conflict plainly. Do not cite chat memory as a document citation; describe
it as previous specialist chat memory. ERP context is trusted operational data,
but it is not document evidence; label it as ERP service history, ERP part
usage, or ERP machine record. Continue citing document evidence with the
required [CITE: ...] format.
""" + "\n" + ERP_ANSWER_RULES


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
    erp_context = retrieve_erp_context(machine_slug)
    if not hits and not erp_context:
        return {
            "answer": "Brak wyników w bazie dla tego zapytania.",
            "citations": [], "hits": [],
        }

    evidence_parts, citations = _build_evidence_parts(hits)
    user_parts = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": f"\n\nQUESTION: {query}\n\nLINKED ERP CONTEXT:\n{format_erp_context(erp_context)}\n\nDOCUMENT EVIDENCE:\n"},
        *evidence_parts,
        {
            "type": "text",
            "text": (
                "\n\nAnswer now. Cite document evidence as [n] inline. "
                "When using ERP data, label it as ERP machine record, ERP "
                "service history, or ERP part usage rather than as a document citation.\n\n"
                f"{ERP_ANSWER_RULES}"
            ),
        },
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
        "erp_context": erp_context,
    }


def _format_transcript(messages: list[dict], limit: int = 14) -> str:
    if not messages:
        return "(no previous messages in this chat)"
    lines = []
    for msg in messages[-limit:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        user = msg.get("username") or "unknown"
        text = (msg.get("text") or "").strip()
        if len(text) > 2500:
            text = text[:2500] + "\n[TRUNCATED]"
        lines.append(f"{role} ({user}, {msg.get('created_at') or '?'}):\n{text}")
    return "\n\n---\n\n".join(lines)


def _format_memory(memories: list[dict]) -> str:
    if not memories:
        return "(no relevant previous specialist chat memory found)"
    lines = []
    for i, msg in enumerate(memories, start=1):
        role = "User" if msg.get("role") == "user" else "Assistant"
        user = msg.get("username") or "unknown"
        score = msg.get("score")
        score_text = f", similarity {float(score):.3f}" if score is not None else ""
        text = (msg.get("text") or "").strip()
        if len(text) > 1800:
            text = text[:1800] + "\n[TRUNCATED]"
        lines.append(
            f"[MEMORY {i}] {role} by {user} in \"{msg.get('session_title') or 'chat'}\""
            f" ({msg.get('created_at') or '?'}{score_text}):\n{text}"
        )
    return "\n\n---\n\n".join(lines)


def chat_answer(
    query: str,
    *,
    transcript: list[dict],
    memories: list[dict],
    machine_slug: str | None = None,
    customer: str | None = None,
    top_k: int = 6,
    deep: bool = False,
) -> dict:
    hits = retrieve(query, top_k=top_k, machine_slug=machine_slug, customer=customer)
    evidence_parts, citations = _build_evidence_parts(hits) if hits else ([], [])
    erp_context = retrieve_erp_context(machine_slug)

    if not hits and not memories and not erp_context:
        return {
            "answer": "Brak wyników w bazie ani w zapisanej historii czatu dla tego zapytania.",
            "citations": [],
            "hits": [],
            "model": DEEP_MODEL if deep else ANSWER_MODEL,
            "memory_count": 0,
            "erp_context": None,
        }

    user_parts = [
        {
            "type": "text",
            "text": (
                "CURRENT CHAT TRANSCRIPT:\n"
                f"{_format_transcript(transcript)}\n\n"
                "RELEVANT PREVIOUS SPECIALIST CHAT MEMORY:\n"
                f"{_format_memory(memories)}\n\n"
                "LINKED ERP CONTEXT:\n"
                f"{format_erp_context(erp_context)}\n\n"
                f"LATEST USER MESSAGE:\n{query}\n\n"
                "DOCUMENT EVIDENCE:\n"
            ),
        },
        *evidence_parts,
        {
            "type": "text",
            "text": (
                "\n\nAnswer now. Prefer current chat and specialist memory over "
                "documents when they conflict. Cite document evidence as [n] "
                "inline when you use it. When using ERP data, label it as ERP "
                "machine record, ERP service history, or ERP part usage.\n\n"
                f"{ERP_ANSWER_RULES}"
            ),
        },
    ]

    model = DEEP_MODEL if deep else ANSWER_MODEL
    answer_text = vision_chat_messages(
        [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": user_parts},
        ],
        deployment=model,
        temperature=0.1,
        max_tokens=2200,
    )
    return {
        "answer": answer_text or "",
        "citations": citations,
        "hits": [
            {k: v for k, v in h.items() if k not in ("merged",)}
            for h in hits
        ],
        "model": model,
        "memory_count": len(memories),
        "erp_context": erp_context,
    }
