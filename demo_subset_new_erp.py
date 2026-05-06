"""Create a small, demo-ready subset from the new ERP API.

Goal:
- prove connection to the new ERP with fresh data
- fetch data only for a few machines (no full ingestion)

This script is read-only:
- POST /get-webToken
- GraphQL reads only

Rate-limit safety:
- throttled calls (default ~1.4 req/s)
- retry + cooldown for "too many requests"
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import requests


DEFAULT_BASE_URL = "https://api.exxas.net"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a small demo subset from new ERP.")
    p.add_argument("--base-url", default=os.environ.get("EXXAS_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--user", default=os.environ.get("EXXAS_USER"))
    p.add_argument("--password", default=os.environ.get("EXXAS_PASSWORD"))
    p.add_argument(
        "--machine-ids",
        default="",
        help="Comma-separated machine IDs (Produkt.id). If empty, auto-pick from freshest service docs.",
    )
    p.add_argument(
        "--auto-pick-count",
        type=int,
        default=3,
        help="How many machines to auto-pick when --machine-ids is empty.",
    )
    p.add_argument(
        "--docs-per-machine",
        type=int,
        default=2,
        help="How many newest service docs to keep per machine.",
    )
    p.add_argument(
        "--scan-pages",
        type=int,
        default=4,
        help="How many pages of newest service docs to scan (subset only).",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="Page size for service docs scan.",
    )
    p.add_argument(
        "--comments-limit",
        type=int,
        default=20,
        help="Max comments per service document.",
    )
    p.add_argument(
        "--parts-limit",
        type=int,
        default=100,
        help="Max DokArtikel rows per service document.",
    )
    p.add_argument(
        "--min-call-interval-ms",
        type=int,
        default=700,
        help="Minimum delay between API calls in milliseconds.",
    )
    p.add_argument(
        "--max-calls",
        type=int,
        default=120,
        help="Hard stop for total API calls in this run.",
    )
    p.add_argument(
        "--output",
        default=f"demo_subset_new_erp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    return p.parse_args()


class ErpClient:
    def __init__(self, base_url: str, user: str, password: str, min_interval_ms: int, max_calls: int):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.min_interval_s = max(0.0, min_interval_ms / 1000.0)
        self.max_calls = max_calls
        self.call_count = 0
        self.last_call_ts = 0.0
        self.graphql_url = ""
        self.headers: dict[str, str] = {}

    def _sleep_if_needed(self):
        now = time.time()
        delta = now - self.last_call_ts
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)

    def _check_budget(self):
        if self.call_count >= self.max_calls:
            raise RuntimeError(f"Max call limit reached ({self.max_calls}).")

    def login(self):
        self._check_budget()
        self._sleep_if_needed()
        r = requests.post(
            f"{self.base_url}/get-webToken",
            json={"user": self.user, "password": self.password},
            timeout=20,
        )
        self.last_call_ts = time.time()
        self.call_count += 1
        r.raise_for_status()
        payload = r.json()
        token = payload.get("bearerToken")
        self.graphql_url = payload.get("graphQlV2Url", "")
        if not token or not self.graphql_url:
            raise RuntimeError("Missing bearerToken or graphQlV2Url in login response.")
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        return payload

    def query(self, query: str, variables: dict[str, Any] | None = None, retries: int = 6) -> dict[str, Any]:
        for attempt in range(retries):
            self._check_budget()
            self._sleep_if_needed()
            r = requests.post(
                self.graphql_url,
                json={"query": query, "variables": variables or {}},
                headers=self.headers,
                timeout=40,
            )
            self.last_call_ts = time.time()
            self.call_count += 1

            data = r.json()
            errs = data.get("errors") or []
            if not errs:
                return data.get("data", {})

            msg = str(errs[0].get("message", "GraphQL error"))
            if "too many requests" in msg.lower() and attempt < retries - 1:
                # Server policy says block can last up to 5 minutes.
                time.sleep(310)
                continue
            raise RuntimeError(msg)
        raise RuntimeError("GraphQL query failed after retries.")


def parse_machine_ids(machine_ids_arg: str) -> list[str]:
    return [x.strip() for x in machine_ids_arg.split(",") if x.strip()]


def load_existing_machine_ids_from_local_graph() -> set[str]:
    try:
        from db import db
        from db_helpers import result_to_dicts

        db.connect()
        rows = result_to_dicts(db.query("MATCH (m:Machine) RETURN m.erp_id AS erp_id"))
        return {str(r["erp_id"]) for r in rows if r.get("erp_id")}
    except Exception:
        return set()


def fetch_recent_service_docs(client: ErpClient, page_size: int, pages: int) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    query = """
    query($limit:Int,$offset:Int){
      Dokument(queryModifiers:{
        limit:$limit,
        offset:$offset,
        groupBy:[id],
        orderBy:[{property:editDate,order:DESC}],
        filters:[{propertyFilter:{property:typ,operator:equals,filterValue:"s"}}]
      }){
        id
        nummer
        bezeichnung
        dokDatum
        createDate
        editDate
        refProdukt { id titel seriennummer }
        refKunde { id nummer }
      }
    }
    """
    for page in range(pages):
        data = client.query(query, {"limit": page_size, "offset": page * page_size})
        rows = data.get("Dokument") or []
        docs.extend(rows)
        if len(rows) < page_size:
            break
    return docs


def auto_pick_machine_ids(
    recent_docs: list[dict[str, Any]],
    pick_count: int,
    existing_ids: set[str],
) -> list[str]:
    picked: list[str] = []
    seen = set()
    for d in recent_docs:
        rp = d.get("refProdukt") or {}
        pid = str(rp.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        # Prefer machines that already exist in current system.
        if existing_ids and pid not in existing_ids:
            continue
        picked.append(pid)
        seen.add(pid)
        if len(picked) >= pick_count:
            break
    # Fallback: if we did not get enough using existing_ids constraint, relax it.
    if len(picked) < pick_count:
        for d in recent_docs:
            rp = d.get("refProdukt") or {}
            pid = str(rp.get("id") or "").strip()
            if not pid or pid in seen:
                continue
            picked.append(pid)
            seen.add(pid)
            if len(picked) >= pick_count:
                break
    return picked


def fetch_machine_by_id(client: ErpClient, machine_id: str) -> dict[str, Any] | None:
    query = """
    query($id:String!){
      Produkt(queryModifiers:{
        limit:1,
        groupBy:[id],
        filters:[{propertyFilter:{property:id,operator:equals,filterValue:$id}}]
      }){
        id
        titel
        seriennummer
        nummer
        createDate
        editDate
        refKunde { id nummer }
      }
    }
    """
    data = client.query(query, {"id": machine_id})
    rows = data.get("Produkt") or []
    return rows[0] if rows else None


def fetch_comments_for_doc(client: ErpClient, doc_id: str, limit: int) -> list[dict[str, Any]]:
    query = """
    query($docId:String!,$limit:Int!){
      Kommentar(queryModifiers:{
        limit:$limit,
        groupBy:[id],
        orderBy:[{property:datum,order:DESC}],
        filters:[
          {propertyFilter:{property:refTyp,operator:equals,filterValue:"dok"}},
          {propertyFilter:{property:refId,operator:equals,filterValue:$docId}}
        ]
      }){
        id
        datum
        kommentar
        refUser
      }
    }
    """
    data = client.query(query, {"docId": doc_id, "limit": limit})
    return data.get("Kommentar") or []


def fetch_parts_for_doc(client: ErpClient, doc_id: str, limit: int) -> list[dict[str, Any]]:
    query = """
    query($docId:String!,$limit:Int!){
      DokArtikel(queryModifiers:{
        limit:$limit,
        groupBy:[id],
        orderBy:[{property:id,order:ASC}],
        filters:[{propertyFilter:{property:refDokId,operator:equals,filterValue:$docId}}]
      }){
        id
        anzahl
        preis
        nummer
        titel
        refArt { id nummer lang1titel herstellernr }
      }
    }
    """
    data = client.query(query, {"docId": doc_id, "limit": limit})
    return data.get("DokArtikel") or []


def main() -> int:
    args = parse_args()

    user = (args.user or "").strip()
    password = args.password or ""
    if not user or not password:
        raise SystemExit("Missing credentials. Use --user/--password or EXXAS_USER/EXXAS_PASSWORD.")

    client = ErpClient(
        base_url=args.base_url,
        user=user,
        password=password,
        min_interval_ms=args.min_call_interval_ms,
        max_calls=args.max_calls,
    )
    client.login()

    existing_ids = load_existing_machine_ids_from_local_graph()
    recent_docs = fetch_recent_service_docs(client, page_size=args.page_size, pages=args.scan_pages)

    machine_ids = parse_machine_ids(args.machine_ids)
    if not machine_ids:
        machine_ids = auto_pick_machine_ids(
            recent_docs=recent_docs,
            pick_count=args.auto_pick_count,
            existing_ids=existing_ids,
        )

    docs_by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in recent_docs:
        rp = d.get("refProdukt") or {}
        pid = str(rp.get("id") or "").strip()
        if pid:
            docs_by_machine[pid].append(d)

    machines_out = []
    for mid in machine_ids:
        machine = fetch_machine_by_id(client, mid)
        if not machine:
            machines_out.append(
                {
                    "machine_id": mid,
                    "error": "Machine not found in new ERP",
                }
            )
            continue

        docs = docs_by_machine.get(mid, [])[: max(1, args.docs_per_machine)]
        docs_out = []
        for d in docs:
            doc_id = str(d.get("id"))
            comments = fetch_comments_for_doc(client, doc_id, args.comments_limit)
            parts = fetch_parts_for_doc(client, doc_id, args.parts_limit)
            docs_out.append(
                {
                    "service_document": d,
                    "comments": comments,
                    "parts": parts,
                }
            )

        machines_out.append(
            {
                "machine_id": mid,
                "exists_in_current_system": (mid in existing_ids) if existing_ids else None,
                "machine": machine,
                "service_documents": docs_out,
            }
        )

    output = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "new_erp_graphql_subset_demo",
        "api_limits_notice": {
            "daily_free_calls": 500,
            "rate_limit_per_user_per_second": 2,
            "throttle_ms_used": args.min_call_interval_ms,
        },
        "selection": {
            "machine_ids_requested": parse_machine_ids(args.machine_ids),
            "machine_ids_used": machine_ids,
            "docs_per_machine": args.docs_per_machine,
            "scan_pages": args.scan_pages,
            "page_size": args.page_size,
        },
        "summary": {
            "machines_in_subset": len(machines_out),
            "recent_service_docs_scanned": len(recent_docs),
            "api_calls_used": client.call_count,
            "max_calls_limit": args.max_calls,
        },
        "machines": machines_out,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(json.dumps(output["summary"], indent=2, ensure_ascii=True))
    print(f"output_file: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
