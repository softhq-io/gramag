"""Benchmark: multimodal (vision + images) vs text-only answer generation.

Runs the same 10 technician-style queries twice:
  A. text-only — vision_desc stripped, no page images attached
  B. multimodal — full pipeline (current)

Retrieval is identical in both conditions (same hits, same order), so the
comparison isolates answer quality from evidence representation.

Output: proto/cache/bench_multimodal.json + side-by-side markdown report.
"""

import json
import time
from pathlib import Path

from ai_client import image_content, vision_chat
from proto import PROTO_CACHE_DIR, resolve_cache
from proto.answer import ANSWER_MODEL, SYSTEM_PROMPT
from proto.retriever import retrieve

OUT_JSON = Path(PROTO_CACHE_DIR) / "bench_multimodal.json"
OUT_MD = Path(PROTO_CACHE_DIR) / "bench_multimodal.md"


QUERIES = [
    {
        "id": "spare-bearing",
        "scope": "smb",
        "q": "Welche Bestellnummer hat das Rillenkugellager in der Umlenkrolle x22781 und wie viele Stück pro Baugruppe?",
    },
    {
        "id": "power-supply-location",
        "scope": "smb",
        "q": "Wo sitzen die Schaltnetzteile T14 und T15 im Gehäuse und welche Klemmleiste ist darunter?",
    },
    {
        "id": "out-of-sequence",
        "scope": "adressiersystem-gui-netjet-1-cag-161-1204-007-00",
        "q": "Adressiersystem meldet 'Out of Sequence' — welche Registry-Parameter prüfen und welche Defaultwerte gelten?",
    },
    {
        "id": "gui-hardware",
        "scope": "adressiersystem-gui-netjet-1-cag-161-1204-007-00",
        "q": "Welche Hardware-Konfiguration wird für den NetJet 1 GUI empfohlen?",
    },
    {
        "id": "abb-bitmap",
        "scope": "adressiersystem-gui-netjet-1-cag-161-1204-007-00",
        "q": "Co zawiera bitmapa ABB_600.bmp i dla jakiego typu przesyłek jest przeznaczona?",
    },
    {
        "id": "eniwa-job",
        "scope": "adressiersystem-gui-netjet-1-cag-161-1204-007-00",
        "q": "Jakie parametry ma zadanie ENIWA — jaki format, jakie liczniki, ile rekordów?",
    },
    {
        "id": "cmc-heizungen",
        "scope": "folieneinschlag-und-adressieranlage-cmc-2800-nr-4282",
        "q": "Wo sitzen die Heizungen an der CMC 2800 und welche Schemata gibt es dafür?",
    },
    {
        "id": "smb-transport",
        "scope": "smb",
        "q": "Wie transportiere ich die SMB S03? Wo sind die Hebepunkte markiert?",
    },
    {
        "id": "cross-schemas",
        "scope": None,
        "q": "Welche Maschinen haben Schaltpläne oder elektrische Schemata dokumentiert?",
    },
    {
        "id": "smb-24m-maintenance",
        "scope": "smb",
        "q": "Welche Baugruppen gehören zur 2.4 Millionen Wartungsstufe und welche Teile werden getauscht?",
    },
]


def build_evidence(hits, *, include_vision: bool, include_images: bool):
    parts = []
    citations = []
    img_count = 0
    max_images = 6

    for i, h in enumerate(hits, start=1):
        label = h["label"]
        machine = h.get("machine_folder") or "?"
        doc = h.get("doc_name") or "?"

        if label == "ManualSection":
            cite = f"[{i}] {machine} / {doc} / page {h.get('page')}"
            vision_block = (
                f"\n\nVISION ANALYSIS:\n{h.get('vision_desc') or ''}"
                if include_vision else ""
            )
            text_block = (
                f"=== EVIDENCE {cite} ===\n"
                f"TEXT EXTRACT:\n{h.get('text') or '(no extractable text)'}"
                f"{vision_block}"
            )
            parts.append({"type": "text", "text": text_block})
            png = h.get("png_path")
            abs_png = resolve_cache(png or "")
            if include_images and abs_png and img_count < max_images and Path(abs_png).exists():
                parts.append({"type": "text", "text": f"(image for [{i}] — page {h.get('page')} of {doc})"})
                parts.append(image_content(abs_png, mime_type="image/png"))
                img_count += 1
            citations.append({"idx": i, "kind": "page", "machine": machine,
                              "doc": doc, "page": h.get("page")})
        elif label == "ConfigFile":
            cite = f"[{i}] {machine} / config: {h.get('name')}"
            summary = h.get("summary") or ""
            if not include_vision:
                # Strip the LLM-generated summary in text-only mode (it's "vision-like" enrichment)
                summary = ""
            parts.append({"type": "text", "text": (
                f"{cite}\nSUMMARY: {summary}\n\n"
                f"CONTENT (truncated):\n{(h.get('content') or '')[:2000]}"
            )})
            citations.append({"idx": i, "kind": "config", "machine": machine,
                              "doc": doc, "name": h.get("name")})
        elif label == "ImageAsset":
            cite = f"[{i}] {machine} / image: {h.get('name')}"
            caption = h.get("caption") or ""
            if not include_vision:
                caption = "(image present, content not analyzed)"
            parts.append({"type": "text", "text": f"{cite}\nCAPTION: {caption}"})
            citations.append({"idx": i, "kind": "image", "machine": machine,
                              "doc": doc, "name": h.get("name")})
    return parts, citations


def ask(query: str, hits: list, *, include_vision: bool, include_images: bool) -> str:
    evidence, _ = build_evidence(hits, include_vision=include_vision, include_images=include_images)
    user_parts = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": f"\n\nQUESTION: {query}\n\nEVIDENCE:\n"},
        *evidence,
        {"type": "text", "text": "\n\nAnswer now, citing sources as [n] inline."},
    ]
    return vision_chat(user_parts, deployment=ANSWER_MODEL, temperature=0.1, max_tokens=2000)


def metrics(answer: str, citations: list) -> dict:
    words = len(answer.split())
    cite_refs = len({c for c in __import__("re").findall(r"\[(\d+)\]", answer)})
    sentences = len([s for s in answer.split(".") if len(s.strip()) > 8])
    return {"words": words, "cited_refs": cite_refs, "sentences": sentences}


def main():
    all_results = []
    for i, case in enumerate(QUERIES, start=1):
        print(f"\n[{i}/{len(QUERIES)}] {case['id']} — {case['q'][:70]}...")
        t0 = time.time()
        hits = retrieve(case["q"], top_k=6, machine_slug=case["scope"], all_clients=True)
        print(f"  retrieved {len(hits)} hits in {time.time()-t0:.1f}s")

        # A. Text-only
        t0 = time.time()
        try:
            text_only = ask(case["q"], hits, include_vision=False, include_images=False)
        except Exception as e:
            text_only = f"ERROR: {e}"
        t_text = time.time() - t0

        # B. Multimodal
        t0 = time.time()
        try:
            multimodal = ask(case["q"], hits, include_vision=True, include_images=True)
        except Exception as e:
            multimodal = f"ERROR: {e}"
        t_mm = time.time() - t0

        cites = [{"idx": i + 1, "label": h["label"],
                  "machine": h.get("machine_folder"), "doc": h.get("doc_name"),
                  "page": h.get("page"), "name": h.get("name"),
                  "score": round(h["score"], 3)}
                 for i, h in enumerate(hits)]

        all_results.append({
            "id": case["id"], "scope": case["scope"], "query": case["q"],
            "hits": cites,
            "text_only": {
                "answer": text_only,
                "latency_s": round(t_text, 1),
                **metrics(text_only, cites),
            },
            "multimodal": {
                "answer": multimodal,
                "latency_s": round(t_mm, 1),
                **metrics(multimodal, cites),
            },
        })
        print(f"  text-only: {all_results[-1]['text_only']['words']}w in {t_text:.1f}s")
        print(f"  multimodal: {all_results[-1]['multimodal']['words']}w in {t_mm:.1f}s")

    OUT_JSON.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n→ JSON: {OUT_JSON}")

    # Markdown report
    md = ["# Multimodal vs Text-Only Benchmark\n"]
    md.append(f"_{len(QUERIES)} queries, same retrieval, two answer conditions._\n")
    total_text = sum(r["text_only"]["words"] for r in all_results)
    total_mm = sum(r["multimodal"]["words"] for r in all_results)
    avg_t_text = sum(r["text_only"]["latency_s"] for r in all_results) / len(all_results)
    avg_t_mm = sum(r["multimodal"]["latency_s"] for r in all_results) / len(all_results)
    md.append(f"\n**Totals:** text-only {total_text} words, multimodal {total_mm} words.")
    md.append(f"**Avg latency:** text-only {avg_t_text:.1f}s, multimodal {avg_t_mm:.1f}s.\n")

    for r in all_results:
        md.append(f"\n---\n\n## {r['id']} — `{r['scope'] or 'cross-machine'}`")
        md.append(f"**Q:** {r['query']}\n")
        md.append(f"**Hits:**")
        for h in r["hits"]:
            ref = f"{h['label']} {h['machine']} / {h['doc'] or h['name']}"
            ref += f" p.{h['page']}" if h.get("page") else ""
            md.append(f"- `{h['score']:.3f}` {ref}")
        md.append("")
        md.append("### Text-only")
        md.append(
            f"_{r['text_only']['words']} words, {r['text_only']['cited_refs']} citations, "
            f"{r['text_only']['latency_s']}s_\n"
        )
        md.append(r["text_only"]["answer"])
        md.append("\n### Multimodal")
        md.append(
            f"_{r['multimodal']['words']} words, {r['multimodal']['cited_refs']} citations, "
            f"{r['multimodal']['latency_s']}s_\n"
        )
        md.append(r["multimodal"]["answer"])
    OUT_MD.write_text("\n".join(md))
    print(f"→ Markdown: {OUT_MD}")


if __name__ == "__main__":
    main()
