"""Link Proto KB machines to ERP machines.

This job reads the existing Proto and ERP FalkorDB graphs. It does not call
Exxas and does not copy the ERP graph into Proto; it only stores a compact
machine link on matching Proto Machine nodes.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from db import db as erp_db
from db_helpers import result_to_dicts
from proto.db_proto import proto_db

DEFAULT_MIN_TITLE_CONFIDENCE = 0.78
DEFAULT_TITLE_MARGIN = 0.08


@dataclass(frozen=True)
class MachineMatch:
    erp_id: str
    erp_customer_id: str | None
    method: str
    confidence: float
    reason: str
    identifier: str | None = None


@dataclass(frozen=True)
class MatchDecision:
    status: str
    match: MachineMatch | None = None
    candidates: tuple[MachineMatch, ...] = ()
    group_identifier: str | None = None


def normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def normalize_identifier(value: str | None) -> str:
    raw = (value or "").lower()
    raw = re.sub(r"\bnr\.?\b", "", raw)
    return re.sub(r"[^a-z0-9]+", "", raw)


def identifier_keys(*values: str | None) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = value or ""
        normalized = normalize_identifier(text)
        if len(normalized) >= 4 and any(ch.isdigit() for ch in normalized):
            keys.add(normalized)
        for token in re.findall(r"[a-zA-Z]*\d[a-zA-Z0-9\-\. ]{2,}\d[a-zA-Z0-9]*", text):
            token_norm = normalize_identifier(token)
            if len(token_norm) >= 4:
                keys.add(token_norm)
        for token in re.findall(r"\b\d{4,}\b", text):
            keys.add(normalize_identifier(token))
    return keys


def best_identifier(common: list[str]) -> str:
    numeric = sorted(
        (item for item in common if item.isdigit()),
        key=lambda item: (len(item), item),
    )
    if numeric:
        return numeric[0]
    return sorted(common, key=lambda item: (len(item), item))[0]


def text_similarity(a: str | None, b: str | None) -> float:
    left = normalize_text(a)
    right = normalize_text(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _customer_compatible(proto: dict, erp: dict) -> bool:
    proto_customer = normalize_text(proto.get("customer"))
    erp_customer = normalize_text(erp.get("customer"))
    if not proto_customer or not erp_customer:
        return False
    if proto_customer == erp_customer:
        return True
    return text_similarity(proto_customer, erp_customer) >= 0.72


def load_overrides(path: str | None) -> dict[str, dict]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for slug, value in raw.items():
        if isinstance(value, str):
            out[slug] = {"erp_id": value}
        elif isinstance(value, list):
            out[slug] = {"erp_ids": [str(item) for item in value]}
        elif isinstance(value, dict) and value.get("erp_id"):
            out[slug] = value
        elif isinstance(value, dict) and value.get("erp_ids"):
            out[slug] = {**value, "erp_ids": [str(item) for item in value["erp_ids"]]}
    return out


def _group_decision(matches: list[MachineMatch], reason: str) -> MatchDecision:
    customer_ids = {m.erp_customer_id for m in matches if m.erp_customer_id}
    identifiers = {m.identifier for m in matches if m.identifier}
    if len(customer_ids) == 1 and len(customer_ids) == len({m.erp_customer_id for m in matches}) and len(identifiers) == 1:
        return MatchDecision(
            "group",
            candidates=tuple(matches),
            group_identifier=next(iter(identifiers)),
        )
    _ = reason
    return MatchDecision("ambiguous", candidates=tuple(matches))


def decide_match(
    proto_machine: dict,
    erp_machines: list[dict],
    *,
    overrides: dict[str, dict] | None = None,
    min_title_confidence: float = DEFAULT_MIN_TITLE_CONFIDENCE,
    title_margin: float = DEFAULT_TITLE_MARGIN,
) -> MatchDecision:
    slug = proto_machine.get("slug") or ""
    override = (overrides or {}).get(slug)
    if override:
        if override.get("erp_ids"):
            ids = [str(item) for item in override["erp_ids"]]
            matches = []
            for erp_id in ids:
                erp = next((m for m in erp_machines if str(m.get("erp_id")) == erp_id), None)
                matches.append(
                    MachineMatch(
                        erp_id=erp_id,
                        erp_customer_id=(erp or {}).get("customer_erp_id"),
                        method="manual_override_group",
                        confidence=1.0,
                        reason=override.get("reason") or "manual group override",
                        identifier=override.get("identifier"),
                    )
                )
            return MatchDecision("group", candidates=tuple(matches), group_identifier=override.get("identifier"))

        erp_id = str(override["erp_id"])
        erp = next((m for m in erp_machines if str(m.get("erp_id")) == erp_id), None)
        return MatchDecision(
            "matched",
            MachineMatch(
                erp_id=erp_id,
                erp_customer_id=(erp or {}).get("customer_erp_id"),
                method="manual_override",
                confidence=1.0,
                reason=override.get("reason") or "manual override",
                identifier=override.get("identifier"),
            ),
        )

    proto_keys = identifier_keys(
        proto_machine.get("serial"),
        proto_machine.get("folder"),
        proto_machine.get("model"),
        proto_machine.get("type"),
    )
    exact: list[MachineMatch] = []
    for erp in erp_machines:
        erp_keys = identifier_keys(
            erp.get("serial_number"),
            erp.get("title"),
            erp.get("new_erp_nummer"),
            erp.get("nummer"),
        )
        common = sorted(proto_keys & erp_keys)
        if common:
            identifier = best_identifier(common)
            exact.append(
                MachineMatch(
                    erp_id=str(erp.get("erp_id")),
                    erp_customer_id=erp.get("customer_erp_id"),
                    method="exact_identifier",
                    confidence=1.0,
                    reason=f"shared identifier {identifier}",
                    identifier=identifier,
                )
            )
    if len(exact) == 1:
        return MatchDecision("matched", exact[0])
    if len(exact) > 1:
        return _group_decision(exact, "multiple exact identifier matches")

    scored: list[MachineMatch] = []
    proto_name = " ".join(
        part for part in (
            proto_machine.get("folder"),
            proto_machine.get("type"),
            proto_machine.get("model"),
        )
        if part
    )
    for erp in erp_machines:
        if not _customer_compatible(proto_machine, erp):
            continue
        score = text_similarity(proto_name, erp.get("title"))
        if score >= min_title_confidence:
            scored.append(
                MachineMatch(
                    erp_id=str(erp.get("erp_id")),
                    erp_customer_id=erp.get("customer_erp_id"),
                    method="customer_title_similarity",
                    confidence=round(score, 3),
                    reason=f"title similarity {score:.3f}",
                    identifier=None,
                )
            )
    scored.sort(key=lambda m: m.confidence, reverse=True)
    if not scored:
        return MatchDecision("unmatched")
    if len(scored) > 1 and scored[0].confidence - scored[1].confidence < title_margin:
        return MatchDecision("ambiguous", candidates=tuple(scored[:5]))
    return MatchDecision("matched", scored[0])


def fetch_proto_machines() -> list[dict]:
    return result_to_dicts(
        proto_db.query(
            """
            MATCH (m:Machine)
            OPTIONAL MATCH (c:Customer)-[:HAS_MACHINE]->(m)
            RETURN m.slug AS slug,
                   m.folder AS folder,
                   m.type AS type,
                   m.model AS model,
                   m.serial AS serial,
                   coalesce(c.name, m.customer) AS customer
            ORDER BY customer, folder
            """
        )
    )


def fetch_erp_machines() -> list[dict]:
    return result_to_dicts(
        erp_db.query(
            """
            MATCH (m:Machine)
            OPTIONAL MATCH (c:Customer)-[:OWNS]->(m)
            RETURN m.erp_id AS erp_id,
                   m.title AS title,
                   m.serial_number AS serial_number,
                   m.new_erp_nummer AS new_erp_nummer,
                   c.erp_id AS customer_erp_id,
                   c.name AS customer
            ORDER BY c.name, m.title
            """
        )
    )


def write_match(slug: str, match: MachineMatch, linked_at: str):
    proto_db.write(
        """
        MATCH (m:Machine {slug: $slug})
        SET m.erp_id = $erp_id,
            m.erp_customer_id = $erp_customer_id,
            m.erp_link_mode = 'single',
            m.erp_related_ids = $erp_related_ids,
            m.erp_related_ids_json = $erp_related_ids_json,
            m.erp_group_identifier = $identifier,
            m.erp_match_method = $method,
            m.erp_match_confidence = $confidence,
            m.erp_match_reason = $reason,
            m.erp_linked_at = $linked_at
        """,
        {
            "slug": slug,
            "erp_id": match.erp_id,
            "erp_customer_id": match.erp_customer_id,
            "erp_related_ids": [match.erp_id],
            "erp_related_ids_json": json.dumps([match.erp_id]),
            "identifier": match.identifier,
            "method": match.method,
            "confidence": match.confidence,
            "reason": match.reason,
            "linked_at": linked_at,
        },
    )


def write_group(slug: str, matches: tuple[MachineMatch, ...], group_identifier: str | None, linked_at: str):
    erp_ids = [match.erp_id for match in matches]
    customer_ids = [match.erp_customer_id for match in matches if match.erp_customer_id]
    proto_db.write(
        """
        MATCH (m:Machine {slug: $slug})
        SET m.erp_id = NULL,
            m.erp_customer_id = $erp_customer_id,
            m.erp_link_mode = 'group',
            m.erp_related_ids = $erp_related_ids,
            m.erp_related_ids_json = $erp_related_ids_json,
            m.erp_group_identifier = $group_identifier,
            m.erp_match_method = 'exact_identifier_group',
            m.erp_match_confidence = $confidence,
            m.erp_match_reason = $reason,
            m.erp_linked_at = $linked_at
        """,
        {
            "slug": slug,
            "erp_customer_id": customer_ids[0] if customer_ids else None,
            "erp_related_ids": erp_ids,
            "erp_related_ids_json": json.dumps(erp_ids),
            "group_identifier": group_identifier,
            "confidence": min((match.confidence for match in matches), default=1.0),
            "reason": f"grouped {len(erp_ids)} ERP records by identifier {group_identifier or '?'}",
            "linked_at": linked_at,
        },
    )


def run_link(
    *,
    overrides: dict[str, dict] | None = None,
    dry_run: bool = False,
    min_title_confidence: float = DEFAULT_MIN_TITLE_CONFIDENCE,
    title_margin: float = DEFAULT_TITLE_MARGIN,
) -> dict[str, Any]:
    proto_db.connect()
    erp_db.connect()
    proto_machines = fetch_proto_machines()
    erp_machines = fetch_erp_machines()
    linked_at = datetime.now(timezone.utc).isoformat()

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "linked_at": linked_at,
        "proto_machine_count": len(proto_machines),
        "erp_machine_count": len(erp_machines),
        "matched": [],
        "grouped": [],
        "unmatched": [],
        "ambiguous": [],
    }

    for machine in proto_machines:
        decision = decide_match(
            machine,
            erp_machines,
            overrides=overrides,
            min_title_confidence=min_title_confidence,
            title_margin=title_margin,
        )
        entry = {
            "slug": machine.get("slug"),
            "folder": machine.get("folder"),
            "customer": machine.get("customer"),
        }
        if decision.status == "matched" and decision.match:
            if not dry_run:
                write_match(str(machine["slug"]), decision.match, linked_at)
            report["matched"].append({
                **entry,
                "erp_id": decision.match.erp_id,
                "erp_customer_id": decision.match.erp_customer_id,
                "method": decision.match.method,
                "confidence": decision.match.confidence,
                "reason": decision.match.reason,
                "identifier": decision.match.identifier,
            })
        elif decision.status == "group":
            if not dry_run:
                write_group(str(machine["slug"]), decision.candidates, decision.group_identifier, linked_at)
            report["grouped"].append({
                **entry,
                "erp_ids": [candidate.erp_id for candidate in decision.candidates],
                "erp_customer_id": next((candidate.erp_customer_id for candidate in decision.candidates if candidate.erp_customer_id), None),
                "method": "exact_identifier_group",
                "confidence": min((candidate.confidence for candidate in decision.candidates), default=1.0),
                "identifier": decision.group_identifier,
                "reason": f"grouped {len(decision.candidates)} ERP records by identifier {decision.group_identifier or '?'}",
                "candidates": [candidate.__dict__ for candidate in decision.candidates],
            })
        elif decision.status == "ambiguous":
            report["ambiguous"].append({
                **entry,
                "candidates": [candidate.__dict__ for candidate in decision.candidates],
            })
        else:
            report["unmatched"].append(entry)

    report["summary"] = {
        "matched": len(report["matched"]),
        "grouped": len(report["grouped"]),
        "unmatched": len(report["unmatched"]),
        "ambiguous": len(report["ambiguous"]),
    }
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Link Proto KB machines to ERP graph machines.")
    parser.add_argument("--overrides", help="JSON file mapping proto machine slug to ERP id or object.")
    parser.add_argument("--report-path", help="Optional JSON report output path.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write Proto graph properties.")
    parser.add_argument("--min-title-confidence", type=float, default=DEFAULT_MIN_TITLE_CONFIDENCE)
    parser.add_argument("--title-margin", type=float, default=DEFAULT_TITLE_MARGIN)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_link(
        overrides=load_overrides(args.overrides),
        dry_run=args.dry_run,
        min_title_confidence=args.min_title_confidence,
        title_margin=args.title_margin,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report_path:
        Path(args.report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_path).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
