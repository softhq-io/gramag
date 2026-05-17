"""Gramag Einsatzplaner — Mission Engine.

Multi-hop graph reasoning + AI briefing generation for service technicians.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from ai_client import chat, json_chat
from db import db
from db_helpers import result_to_dicts, result_single
from retriever import graph_vector_search
from embeddings import generate_query_embedding


# ── Search ───────────────────────────────────────────────────────────


def search_machines(query: str, limit: int = 10) -> list[dict]:
    """Fulltext search on Machine.title, returns machine + customer + type + brand."""
    result = db.query(
        """
        CALL db.idx.fulltext.queryNodes('Machine', $query)
        YIELD node, score
        WITH node AS m, score
        ORDER BY score DESC LIMIT $limit
        OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
        OPTIONAL MATCH (m)-[:IS_TYPE]->(mt:MachineType)
        OPTIONAL MATCH (m)-[:MADE_BY]->(mb:MachineBrand)
        RETURN m.erp_id AS erp_id, m.title AS title,
               m.serial_number AS serial_number,
               c.name AS customer, c.erp_id AS customer_erp_id,
               mt.name AS machine_type, mb.name AS brand,
               score
        """,
        {"query": query, "limit": limit},
    )
    return result_to_dicts(result)


def search_customers(query: str, limit: int = 10) -> list[dict]:
    """Fulltext search on Customer.name, returns customer + machine count."""
    result = db.query(
        """
        CALL db.idx.fulltext.queryNodes('Customer', $query)
        YIELD node, score
        WITH node AS c, score
        ORDER BY score DESC LIMIT $limit
        OPTIONAL MATCH (c)-[:OWNS]->(m:Machine)
        RETURN c.erp_id AS erp_id, c.name AS name, c.city AS city,
               count(m) AS machine_count, score
        """,
        {"query": query, "limit": limit},
    )
    return result_to_dicts(result)


# ── Machine Detail ───────────────────────────────────────────────────


def get_machine_detail(erp_id: str) -> dict | None:
    """Full machine context: customer, type, brand, serial."""
    result = db.query(
        """
        MATCH (m:Machine {erp_id: $erp_id})
        OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
        OPTIONAL MATCH (m)-[:IS_TYPE]->(mt:MachineType)
        OPTIONAL MATCH (m)-[:MADE_BY]->(mb:MachineBrand)
        RETURN m.erp_id AS erp_id, m.title AS title,
               m.serial_number AS serial_number,
               c.name AS customer, c.erp_id AS customer_erp_id, c.city AS city,
               mt.name AS machine_type, mb.name AS brand
        """,
        {"erp_id": erp_id},
    )
    return result_single(result)


# ── Similar Cases ────────────────────────────────────────────────────


def find_similar_cases(
    machine_erp_id: str, symptom: str = "", limit: int = 8
) -> list[dict]:
    """Multi-hop: find service jobs on same MachineType from other machines."""
    result = db.query(
        """
        MATCH (target:Machine {erp_id: $erp_id})-[:IS_TYPE]->(mt:MachineType)
        MATCH (other:Machine)-[:IS_TYPE]->(mt)
        WHERE other.erp_id <> $erp_id
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(other)
        OPTIONAL MATCH (c:Customer)-[:OWNS]->(other)
        OPTIONAL MATCH (sj)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        OPTIONAL MATCH (sc:ServiceComment)-[:ON_JOB]->(sj)
        WITH other, c, sj, mt,
             collect(DISTINCT {nummer: p.nummer, titel: p.titel})[0..10] AS parts_used,
             collect(DISTINCT {author: sc.author, text: sc.text, date: sc.date})[0..3] AS comments
        ORDER BY sj.date DESC
        LIMIT $limit
        RETURN sj.erp_id AS job_erp_id, sj.title AS job_title,
               sj.date AS job_date, sj.nummer AS job_nummer,
               sj.description AS job_description,
               other.title AS machine_title, other.erp_id AS machine_erp_id,
               c.name AS customer,
               mt.name AS machine_type,
               parts_used, comments
        """,
        {"erp_id": machine_erp_id, "limit": limit},
    )
    cases = result_to_dicts(result)

    # Fallback: if no IS_TYPE edges, find jobs on other machines (demo subset)
    if not cases:
        result = db.query(
            """
            MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(other:Machine)
            WHERE other.erp_id <> $erp_id
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(other)
            OPTIONAL MATCH (sj)-[:USED_PART]->(p:Part)
            WHERE p IS NULL OR NOT p.noise
            OPTIONAL MATCH (sc:ServiceComment)-[:ON_JOB]->(sj)
            WITH other, c, sj,
                 collect(DISTINCT {nummer: p.nummer, titel: p.titel})[0..10] AS parts_used,
                 collect(DISTINCT {text: sc.text, date: sc.date})[0..3] AS comments
            ORDER BY sj.date DESC
            LIMIT $limit
            RETURN sj.erp_id AS job_erp_id, sj.title AS job_title,
                   sj.date AS job_date, sj.nummer AS job_nummer,
                   sj.description AS job_description,
                   other.title AS machine_title, other.erp_id AS machine_erp_id,
                   c.name AS customer,
                   'Andere Maschine' AS machine_type,
                   parts_used, comments
            """,
            {"erp_id": machine_erp_id, "limit": limit},
        )
        cases = result_to_dicts(result)

    # If symptom provided, boost cases with matching text
    if symptom:
        symptom_lower = symptom.lower()
        for case in cases:
            title = (case.get("job_title") or "").lower()
            cmts = " ".join((c.get("text") or "") if isinstance(c, dict) else str(c) for c in (case.get("comments") or [])).lower()
            if symptom_lower in title or symptom_lower in cmts:
                case["symptom_match"] = True
        # Sort: symptom matches first
        cases.sort(key=lambda c: (not c.get("symptom_match", False)))

    return cases


def summarize_similar_cases(cases: list[dict], symptom: str = "") -> list[dict]:
    """Generate a short LLM summary for each similar case in one batch call."""
    if not cases:
        return cases

    lines = []
    for i, c in enumerate(cases):
        parts = ", ".join(
            f"{p.get('nummer', '?')} ({p.get('titel', '')})"
            for p in (c.get("parts_used") or [])[:5]
        )
        cmts = " | ".join(
            (cm.get("text") or "")[:200]
            for cm in (c.get("comments") or [])
            if isinstance(cm, dict) and cm.get("text")
        )
        desc = (c.get("job_description") or "")[:300]
        lines.append(
            f"CASE {i}: [{c.get('job_date', '?')}] {c.get('job_title', '?')} "
            f"@ {c.get('customer', '?')}\n"
            f"  Beschreibung: {desc or 'keine'}\n"
            f"  Teile: {parts or 'keine'}\n"
            f"  Kommentare: {cmts or 'keine'}"
        )

    prompt = (
        "Du bist ein Service-Assistent für Gramag (Schweiz, grafische Maschinen).\n"
        "Fasse jeden der folgenden Service-Fälle in GENAU einem Satz zusammen.\n"
        "Fokussiere auf: Was war das Problem? Was wurde gemacht/gelöst?\n"
        f"{('Aktuelles Symptom: ' + symptom + chr(10)) if symptom else ''}"
        "Antworte als JSON-Objekt mit dem Feld summaries (Array mit Strings, ein Eintrag pro Case).\n"
        "Beispiel: {\"summaries\":[\"Riemen gerissen, ersetzt durch Teil 24046.\", \"Sensor defekt, kalibriert.\"]}\n\n"
        + "\n\n".join(lines)
    )

    try:
        parsed = json.loads(json_chat(prompt, temperature=0.2, max_tokens=2000))
        summaries = parsed.get("summaries", []) if isinstance(parsed, dict) else []
        for i, case in enumerate(cases):
            case["llm_summary"] = summaries[i] if i < len(summaries) else ""
    except Exception as e:
        print(f"[WARN] summarize_similar_cases failed: {e}")
        for case in cases:
            case.setdefault("llm_summary", "")

    return cases


# ── Parts Kit ────────────────────────────────────────────────────────


def _is_noise_part(titel: str) -> bool:
    """Runtime noise filter for parts that slipped past the graph flag."""
    t = (titel or "").lower()
    for kw in ("dummy", "wegpauschale", "pauschale kleinmaterial",
               "pauschale km", "tagespauschale"):
        if kw in t:
            return True
    return False


def build_parts_kit(machine_erp_id: str) -> dict:
    """Three-layer parts recommendation with job context.

    1. Parts used on this specific machine (sorted by frequency)
    2. Parts used on all machines of the same type
    3. Co-occurrence (OFTEN_USED_WITH) for top parts
    """
    # Layer 1: this machine — with job context
    r1 = db.query(
        """
        MATCH (m:Machine {erp_id: $erp_id})
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
        MATCH (sj)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        WITH p, sj
        ORDER BY sj.date DESC
        WITH p, count(sj) AS frequency,
             collect(DISTINCT sj.title)[0..3] AS job_titles
        RETURN p.nummer AS nummer, p.titel AS titel,
               p.manufacturer_nr AS manufacturer_nr,
               frequency, job_titles
        ORDER BY frequency DESC
        LIMIT 20
        """,
        {"erp_id": machine_erp_id},
    )
    machine_parts = [p for p in result_to_dicts(r1)
                     if not _is_noise_part(p.get("titel", ""))]

    # Layer 2: same type — with job context
    r2 = db.query(
        """
        MATCH (target:Machine {erp_id: $erp_id})-[:IS_TYPE]->(mt:MachineType)
        MATCH (other:Machine)-[:IS_TYPE]->(mt)
        WHERE other.erp_id <> $erp_id
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(other)
        MATCH (sj)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        WITH p, sj
        ORDER BY sj.date DESC
        WITH p, count(DISTINCT sj) AS frequency,
             collect(DISTINCT sj.title)[0..3] AS job_titles
        RETURN p.nummer AS nummer, p.titel AS titel,
               p.manufacturer_nr AS manufacturer_nr,
               frequency, job_titles
        ORDER BY frequency DESC
        LIMIT 20
        """,
        {"erp_id": machine_erp_id},
    )
    type_parts = [p for p in result_to_dicts(r2)
                  if not _is_noise_part(p.get("titel", ""))]

    # Layer 3: co-occurrence from top machine parts
    co_parts = []
    top_nummers = [p["nummer"] for p in machine_parts[:5] if p.get("nummer")]
    if not top_nummers:
        top_nummers = [p["nummer"] for p in type_parts[:5] if p.get("nummer")]
    if top_nummers:
        r3 = db.query(
            """
            MATCH (p:Part)-[r:OFTEN_USED_WITH]-(p2:Part)
            WHERE p.nummer IN $nummers AND NOT p2.noise
            RETURN DISTINCT p2.nummer AS nummer, p2.titel AS titel,
                   p2.manufacturer_nr AS manufacturer_nr,
                   r.count AS co_count
            ORDER BY co_count DESC
            LIMIT 15
            """,
            {"nummers": top_nummers},
        )
        co_parts = [p for p in result_to_dicts(r3)
                    if not _is_noise_part(p.get("titel", ""))]

    return {
        "machine_parts": machine_parts,
        "type_parts": type_parts,
        "co_occurrence_parts": co_parts,
    }


def summarize_parts_kit(kit: dict, machine: dict, symptom: str = "") -> str:
    """Generate LLM summary explaining why these parts are recommended."""
    lines = []
    machine_title = machine.get("title", "?")
    machine_type = machine.get("machine_type", "?")

    if kit["machine_parts"]:
        lines.append("DIESE MASCHINE (historisch):")
        for p in kit["machine_parts"][:10]:
            jobs = ", ".join(p.get("job_titles") or [])
            lines.append(f"  - {p['nummer']} {p.get('titel', '?')} ({p.get('frequency', 0)}x) Jobs: {jobs or '?'}")

    if kit["type_parts"]:
        lines.append(f"\nGLEICHER TYP ({machine_type}):")
        for p in kit["type_parts"][:10]:
            jobs = ", ".join(p.get("job_titles") or [])
            lines.append(f"  - {p['nummer']} {p.get('titel', '?')} ({p.get('frequency', 0)}x) Jobs: {jobs or '?'}")

    if kit["co_occurrence_parts"]:
        lines.append("\nOFT ZUSAMMEN VERWENDET:")
        for p in kit["co_occurrence_parts"][:5]:
            lines.append(f"  - {p['nummer']} {p.get('titel', '?')}")

    prompt = (
        "Du bist ein Service-Assistent für Gramag (Schweiz, grafische Maschinen).\n"
        f"Maschine: {machine_title} (Typ: {machine_type})\n"
        f"{('Symptom: ' + symptom + chr(10)) if symptom else ''}"
        "Erstelle eine Packliste für den Techniker mit Markdown.\n"
        "Regeln:\n"
        "- Starte DIREKT mit den Empfehlungen, keine Einleitung.\n"
        "- Gruppiere nach Priorität: **Kritisch**, **Empfohlen**, **Optional**.\n"
        "- Nenne Teilenummern in `code`-Format.\n"
        "- Erkläre in 1 Halbsatz WARUM (z.B. 'häufiger Verschleiß bei Revisionen').\n"
        "- Max 3-4 Gruppen, je 2-4 Teile.\n\n"
        + "\n".join(lines)
    )

    try:
        return chat(prompt, temperature=0.2, max_tokens=4000).strip()
    except Exception as e:
        print(f"[WARN] summarize_parts_kit failed: {e}")
        return ""


# ── Service History ──────────────────────────────────────────────────


def get_service_history(machine_erp_id: str, limit: int = 20) -> list[dict]:
    """ServiceJobs + comments + parts for a machine."""
    result = db.query(
        """
        MATCH (m:Machine {erp_id: $erp_id})
        MATCH (sj:ServiceJob)-[:FOR_MACHINE]->(m)
        OPTIONAL MATCH (sc:ServiceComment)-[:ON_JOB]->(sj)
        OPTIONAL MATCH (sj)-[:USED_PART]->(p:Part)
        WHERE NOT p.noise
        WITH sj,
             collect(DISTINCT {author: sc.author, text: sc.text})[0..5] AS comments,
             collect(DISTINCT {nummer: p.nummer, titel: p.titel})[0..10] AS parts
        ORDER BY sj.date DESC
        LIMIT $limit
        RETURN sj.erp_id AS erp_id, sj.title AS title,
               sj.nummer AS nummer, sj.date AS date,
               sj.description AS description,
               comments, parts
        """,
        {"erp_id": machine_erp_id, "limit": limit},
    )
    return result_to_dicts(result)


# ── Manual References ────────────────────────────────────────────────


def find_relevant_manuals(query: str, machine_erp_id: str = "") -> list[dict]:
    """Vector search on ManualSection + boost for matching brand."""
    query_emb = generate_query_embedding(query)
    results = graph_vector_search(query_emb, top_k=20)

    # Fallback: search proto KB if main graph has no ManualSections
    if not results:
        try:
            from proto.retriever import retrieve as proto_retrieve
            hits = proto_retrieve(query, top_k=10)
            for h in hits:
                text = h.get("merged") or h.get("text") or h.get("vision_desc") or ""
                if text.strip():
                    results.append({
                        "text": text[:600],
                        "summary": h.get("summary", ""),
                        "source": f"Manual: {h.get('doc_name', '?')}",
                        "supplier": h.get("machine_slug", ""),
                        "score": h.get("score", 0),
                    })
        except Exception:
            pass

    # If machine provided, boost results matching its brand
    brand = ""
    if machine_erp_id:
        detail = get_machine_detail(machine_erp_id)
        brand = ((detail or {}).get("brand") or "").lower()
        if brand:
            for r in results:
                supplier = (r.get("supplier") or "").lower()
                if brand in supplier or supplier in brand:
                    r["score"] = r.get("score", 0) + 0.2
                    r["brand_match"] = True
            results.sort(key=lambda r: r.get("score", 0), reverse=True)

    # If brand is known, only return brand-matching results
    if brand:
        results = [r for r in results if r.get("brand_match")]

    # Filter out low-relevance results (brand match already ensures relevance)
    min_score = 0.1 if brand else 0.5
    results = [r for r in results if r.get("score", 0) >= min_score]

    _SCHEMATIC_KEYWORDS = (
        "schematic diagram", "schematic diagrams",
        "wiring diagram", "wiring schematic", "wiring schematics",
        "circuit diagram", "electrical diagram", "electrical diagrams",
        "title block", "electrical schematic", "electrical schematics",
        "connection diagram", "block diagram",
        "revision history",
        "diagrams and schematics", "schematics and diagrams",
        "contains diagrams", "contains schematics",
    )

    def _is_garbage(text: str) -> bool:
        """Detect CAD schematics and title-block metadata — not useful prose."""
        if not text:
            return True
        if "\\" in text or "document path" in text.lower():
            return True
        tl = text.lower()
        if any(kw in tl for kw in _SCHEMATIC_KEYWORDS):
            return True
        words = text.split()
        noise = sum(1 for w in words if len(w) <= 1 or w.isdigit())
        return bool(words) and (noise / len(words)) > 0.4

    def _snippet(r: dict) -> str:
        text = (r.get("text") or "").strip()
        summary = (r.get("summary") or "").strip()
        content = text if not _is_garbage(text) else (summary if not _is_garbage(summary) else "")
        if not content:
            return ""
        return content[:300].rsplit(" ", 1)[0] + "…" if len(content) > 300 else content

    # Drop results where both text and summary are garbage (pure schematic pages)
    results = [r for r in results if _snippet(r)]

    return [
        {
            "title": r.get("source", "").replace("Manual: ", ""),
            "snippet": _snippet(r),
            "supplier": r.get("supplier", ""),
            "score": round(r.get("score", 0), 3),
            "brand_match": r.get("brand_match", False),
        }
        for r in results[:6]
    ]


# ── Briefing Generator ──────────────────────────────────────────────


def generate_briefing(machine_erp_id: str, symptom: str = "") -> dict:
    """Orchestrate all functions and produce an AI briefing."""
    reasoning_path = []

    # Step 1: Machine detail (needed by later steps)
    machine = get_machine_detail(machine_erp_id)
    if not machine:
        return {"error": "Machine not found", "erp_id": machine_erp_id}
    reasoning_path.append(
        {"step": "Machine Lookup", "detail": machine.get("title", "?")}
    )

    # Steps 2-5: run in parallel
    history = []
    similar = []
    parts_kit = {}
    manuals = []

    def _do_history():
        return get_service_history(machine_erp_id, limit=15)

    def _do_similar():
        return find_similar_cases(machine_erp_id, symptom=symptom, limit=8)

    def _do_parts():
        return build_parts_kit(machine_erp_id)

    def _do_manuals():
        manual_query = symptom if symptom else (machine.get("title") or "")
        return find_relevant_manuals(manual_query, machine_erp_id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        fut_history = pool.submit(_do_history)
        fut_similar = pool.submit(_do_similar)
        fut_parts = pool.submit(_do_parts)
        fut_manuals = pool.submit(_do_manuals)

        history = fut_history.result()
        similar = fut_similar.result()
        parts_kit = fut_parts.result()
        manuals = fut_manuals.result()

    reasoning_path.append(
        {"step": "Service History", "detail": f"{len(history)} jobs found"}
    )
    reasoning_path.append(
        {"step": "Similar Cases", "detail": f"{len(similar)} cases from same type"}
    )
    total_parts = (
        len(parts_kit.get("machine_parts", []))
        + len(parts_kit.get("type_parts", []))
        + len(parts_kit.get("co_occurrence_parts", []))
    )
    reasoning_path.append(
        {"step": "Parts Kit", "detail": f"{total_parts} parts across 3 layers"}
    )
    reasoning_path.append(
        {"step": "Manual Refs", "detail": f"{len(manuals)} relevant sections"}
    )

    # Step 6: AI summary (needs all data)
    reasoning_path.append({"step": "AI Briefing", "detail": "Generating summary..."})
    context = _build_briefing_context(machine, history, similar, parts_kit, manuals, symptom)
    summary = _generate_summary(context)
    reasoning_path[-1]["detail"] = "Complete"

    return {
        "machine": machine,
        "symptom": symptom,
        "summary": summary,
        "history": history,
        "similar_cases": similar,
        "parts_kit": parts_kit,
        "manuals": manuals,
        "reasoning_path": reasoning_path,
    }


def _build_briefing_context(
    machine: dict,
    history: list[dict],
    similar: list[dict],
    parts_kit: dict,
    manuals: list[dict],
    symptom: str,
) -> str:
    """Build the context string for the service briefing prompt."""
    lines = []
    lines.append(f"MACHINE: {machine.get('title', '?')}")
    lines.append(f"Customer: {machine.get('customer', '?')} ({machine.get('city', '?')})")
    lines.append(f"Type: {machine.get('machine_type', '?')}, Brand: {machine.get('brand', '?')}")
    lines.append(f"Serial: {machine.get('serial_number', '?')}")
    if symptom:
        lines.append(f"\nREPORTED SYMPTOM: {symptom}")

    lines.append(f"\nSERVICE HISTORY ({len(history)} jobs):")
    for h in history[:8]:
        parts_text = ", ".join(
            (p.get("nummer") or "?") for p in (h.get("parts") or [])[:5]
        )
        lines.append(f"  - [{h.get('date', '?')}] {h.get('title', '?')} | Parts: {parts_text or 'none'}")
        for c in (h.get("comments") or [])[:2]:
            text = (c.get("text") or "") if isinstance(c, dict) else str(c or "")
            if text:
                lines.append(f"    Comment: {text[:150]}")

    lines.append(f"\nSIMILAR CASES ({len(similar)} from same machine type):")
    for s in similar[:5]:
        parts_text = ", ".join(
            (p.get("nummer") or "?") for p in (s.get("parts_used") or [])[:5]
        )
        match_tag = " [SYMPTOM MATCH]" if s.get("symptom_match") else ""
        lines.append(
            f"  - [{s.get('job_date', '?')}] {s.get('job_title', '?')}{match_tag} "
            f"@ {s.get('customer', '?')} | Parts: {parts_text or 'none'}"
        )

    lines.append(f"\nTOP PARTS (this machine):")
    for p in parts_kit.get("machine_parts", [])[:10]:
        lines.append(f"  - [{p.get('nummer')}] {p.get('titel', '?')} (used {p.get('frequency', 0)}x)")

    lines.append(f"\nRELEVANT MANUALS:")
    for m in manuals[:4]:
        lines.append(f"  - {m.get('title', '?')} ({m.get('supplier', '?')}) score={m.get('score', 0)}")

    return "\n".join(lines)


def _generate_summary(context: str) -> str:
    """Call Azure OpenAI to produce a concise service briefing."""
    prompt = f"""Du bist ein Service-Briefing-Assistent für Gramag Grafische Maschinen AG (Schweiz).
Erstelle ein prägnantes Einsatz-Briefing für den Servicetechniker basierend auf den folgenden Daten.

Struktur:
1. **Maschinenübersicht** — Kurze Zusammenfassung der Maschine und des Kunden
2. **Symptom-Analyse** — Was ist das Problem? Was zeigen ähnliche Fälle?
3. **Empfohlene Teile** — Welche Ersatzteile sollte der Techniker mitnehmen?
4. **Bekannte Lösungen** — Tipps aus der Service-Historie und Handbüchern
5. **Hinweise** — Besondere Hinweise oder Warnungen

Regeln:
- Antworte auf Deutsch.
- Sei prägnant und praktisch.
- Nenne konkrete Teilenummern.
- Wenn ein Symptom angegeben ist, fokussiere darauf.

DATEN:
{context}"""

    try:
        return chat(prompt, temperature=0.3, max_tokens=4000)
    except Exception as e:
        return f"Briefing-Generierung fehlgeschlagen: {e}"
