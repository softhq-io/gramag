"""Daily Exxas GraphQL sync into the local FalkorDB graph.

The sync is intentionally read-only against Exxas and idempotent against
FalkorDB. It fetches app-facing ERP data only: machines, customers, service
documents, comments, and used parts.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from db import db
from db_helpers import result_single, result_to_dicts, result_value
from import_new_erp_subset import (
    upsert_comment,
    upsert_machine,
    upsert_part_and_edge,
    upsert_service_job,
)


DEFAULT_BASE_URL = "https://api.exxas.net"
SYNC_NAME = "exxas_daily"
ERP_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class SyncCounts:
    machines_touched: int = 0
    service_docs_touched: int = 0
    comments_touched: int = 0
    parts_rows_touched: int = 0
    changed_docs_seen: int = 0
    changed_comments_seen: int = 0
    changed_machines_seen: int = 0
    rejected_rows: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def reject(self, reason: str):
        self.rejected_rows += 1
        self.rejected_reasons[reason] += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "machines_touched": self.machines_touched,
            "service_docs_touched": self.service_docs_touched,
            "comments_touched": self.comments_touched,
            "parts_rows_touched": self.parts_rows_touched,
            "changed_docs_seen": self.changed_docs_seen,
            "changed_comments_seen": self.changed_comments_seen,
            "changed_machines_seen": self.changed_machines_seen,
            "rejected_rows": self.rejected_rows,
            "rejected_reasons": dict(self.rejected_reasons),
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat()


def parse_erp_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in (ERP_TS_FORMAT, "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def format_erp_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ERP_TS_FORMAT)


def max_erp_ts(values: list[str | None]) -> str | None:
    parsed = [parse_erp_ts(v) for v in values if v]
    parsed = [v for v in parsed if v is not None]
    if not parsed:
        return None
    return format_erp_ts(max(parsed))


class ExxasClient:
    def __init__(
        self,
        base_url: str,
        user: str,
        password: str,
        min_interval_ms: int,
        max_calls: int,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.user = user
        self.password = password
        self.min_interval_s = max(0.0, min_interval_ms / 1000.0)
        self.max_calls = max_calls
        self.call_count = 0
        self.last_call_ts = 0.0
        self.graphql_url = ""
        self.headers: dict[str, str] = {}
        self.session = session or requests.Session()

    def _sleep_if_needed(self):
        delta = time.time() - self.last_call_ts
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)

    def _check_budget(self):
        if self.call_count >= self.max_calls:
            raise RuntimeError(f"Max Exxas API call limit reached ({self.max_calls}).")

    def login(self):
        self._check_budget()
        self._sleep_if_needed()
        r = self.session.post(
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
            raise RuntimeError("Missing bearerToken or graphQlV2Url in Exxas login response.")
        self.headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    def query(self, query: str, variables: dict[str, Any] | None = None, retries: int = 6) -> dict[str, Any]:
        for attempt in range(retries):
            self._check_budget()
            self._sleep_if_needed()
            r = self.session.post(
                self.graphql_url,
                json={"query": query, "variables": variables or {}},
                headers=self.headers,
                timeout=45,
            )
            self.last_call_ts = time.time()
            self.call_count += 1
            r.raise_for_status()
            data = r.json()
            errors = data.get("errors") or []
            if not errors:
                return data.get("data", {})
            message = str(errors[0].get("message", "GraphQL error"))
            if "too many requests" in message.lower() and attempt < retries - 1:
                time.sleep(310)
                continue
            raise RuntimeError(message)
        raise RuntimeError("GraphQL query failed after retries.")


SERVICE_DOC_FIELDS = """
id
nummer
typ
bezeichnung
dokDatum
createDate
editDate
refProdukt { id titel seriennummer }
refKunde { id nummer }
"""

MACHINE_FIELDS = """
id
titel
seriennummer
nummer
createDate
editDate
refKunde { id nummer }
"""

COMMENT_FIELDS = """
id
datum
kommentar
refUser
refTyp
refId
"""

PART_FIELDS = """
id
anzahl
preis
nummer
titel
refArt { id nummer lang1titel herstellernr }
"""


def query_list(client: ExxasClient, root: str, body: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
    data = client.query(body, variables)
    return data.get(root) or []


def fetch_service_doc_by_id(client: ExxasClient, doc_id: str) -> dict[str, Any] | None:
    query = f"""
    query($id:String!){{
      Dokument(queryModifiers:{{
        limit:1,
        groupBy:[id],
        filters:[{{propertyFilter:{{property:id,operator:equals,filterValue:$id}}}}]
      }}){{ {SERVICE_DOC_FIELDS} }}
    }}
    """
    rows = query_list(client, "Dokument", query, {"id": doc_id})
    return rows[0] if rows else None


def fetch_machine_by_id(client: ExxasClient, machine_id: str) -> dict[str, Any] | None:
    query = f"""
    query($id:String!){{
      Produkt(queryModifiers:{{
        limit:1,
        groupBy:[id],
        filters:[{{propertyFilter:{{property:id,operator:equals,filterValue:$id}}}}]
      }}){{ {MACHINE_FIELDS} }}
    }}
    """
    rows = query_list(client, "Produkt", query, {"id": machine_id})
    return rows[0] if rows else None


def fetch_comments_for_doc(client: ExxasClient, doc_id: str, limit: int = 200) -> list[dict[str, Any]]:
    query = f"""
    query($docId:String!,$limit:Int!){{
      Kommentar(queryModifiers:{{
        limit:$limit,
        groupBy:[id],
        orderBy:[{{property:datum,order:DESC}}],
        filters:[
          {{propertyFilter:{{property:refTyp,operator:equals,filterValue:"dok"}}}},
          {{propertyFilter:{{property:refId,operator:equals,filterValue:$docId}}}}
        ]
      }}){{ {COMMENT_FIELDS} }}
    }}
    """
    return query_list(client, "Kommentar", query, {"docId": doc_id, "limit": limit})


def fetch_parts_for_doc(client: ExxasClient, doc_id: str, limit: int = 300) -> list[dict[str, Any]]:
    query = f"""
    query($docId:String!,$limit:Int!){{
      DokArtikel(queryModifiers:{{
        limit:$limit,
        groupBy:[id],
        orderBy:[{{property:id,order:ASC}}],
        filters:[{{propertyFilter:{{property:refDokId,operator:equals,filterValue:$docId}}}}]
      }}){{ {PART_FIELDS} }}
    }}
    """
    return query_list(client, "DokArtikel", query, {"docId": doc_id, "limit": limit})


def fetch_changed_with_filter(
    client: ExxasClient,
    root: str,
    fields: str,
    timestamp_field: str,
    since: str,
    extra_filters: str = "",
    page_size: int = 200,
) -> list[dict[str, Any]]:
    filters = f"{{propertyFilter:{{property:{timestamp_field},operator:greaterThanOrEqual,filterValue:$since}}}}"
    if extra_filters:
        filters = f"{extra_filters}, {filters}"
    query = f"""
    query($limit:Int!,$offset:Int!,$since:String!){{
      {root}(queryModifiers:{{
        limit:$limit,
        offset:$offset,
        groupBy:[id],
        orderBy:[{{property:{timestamp_field},order:DESC}}],
        filters:[{filters}]
      }}){{ {fields} }}
    }}
    """
    return fetch_pages(client, root, query, {"since": since}, page_size=page_size)


def fetch_changed_by_scan(
    client: ExxasClient,
    root: str,
    fields: str,
    timestamp_field: str,
    since: str,
    extra_filters: str = "",
    page_size: int = 200,
) -> list[dict[str, Any]]:
    filter_clause = f", filters:[{extra_filters}]" if extra_filters else ""
    query = f"""
    query($limit:Int!,$offset:Int!){{
      {root}(queryModifiers:{{
        limit:$limit,
        offset:$offset,
        groupBy:[id],
        orderBy:[{{property:{timestamp_field},order:DESC}}]
        {filter_clause}
      }}){{ {fields} }}
    }}
    """
    rows: list[dict[str, Any]] = []
    cutoff = parse_erp_ts(since)
    for page in range(1000):
        page_rows = query_list(client, root, query, {"limit": page_size, "offset": page * page_size})
        if not page_rows:
            break
        stop = False
        for row in page_rows:
            row_ts = parse_erp_ts(row.get(timestamp_field))
            if cutoff and row_ts and row_ts < cutoff:
                stop = True
                continue
            rows.append(row)
        if stop or len(page_rows) < page_size:
            break
    return rows


def fetch_changed(
    client: ExxasClient,
    root: str,
    fields: str,
    timestamp_field: str,
    since: str,
    extra_filters: str = "",
    page_size: int = 200,
) -> list[dict[str, Any]]:
    try:
        return fetch_changed_with_filter(
            client, root, fields, timestamp_field, since, extra_filters, page_size
        )
    except RuntimeError as exc:
        message = str(exc).lower()
        if "max exxas api call" in message or "too many requests" in message:
            raise
        return fetch_changed_by_scan(
            client, root, fields, timestamp_field, since, extra_filters, page_size
        )


def fetch_pages(
    client: ExxasClient,
    root: str,
    query: str,
    variables: dict[str, Any] | None = None,
    page_size: int = 200,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    variables = dict(variables or {})
    for page in range(1000):
        page_vars = {**variables, "limit": page_size, "offset": page * page_size}
        page_rows = query_list(client, root, query, page_vars)
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            break
    return rows


def get_state_watermark() -> str | None:
    result = db.query(
        """
        MATCH (s:SyncState {name: $name})
        RETURN s.watermark AS watermark
        """,
        {"name": SYNC_NAME},
    )
    return result_value(result, "watermark")


def derive_initial_watermark() -> str | None:
    result = db.query(
        """
        MATCH (sj:ServiceJob)
        WHERE sj.new_erp_edit_date IS NOT NULL
        RETURN max(sj.new_erp_edit_date) AS watermark
        """
    )
    return result_value(result, "watermark")


def effective_watermark(watermark: str, lookback_hours: int) -> str:
    parsed = parse_erp_ts(watermark)
    if not parsed:
        raise RuntimeError(f"Invalid Exxas watermark: {watermark!r}")
    return format_erp_ts(parsed - timedelta(hours=lookback_hours))


def acquire_lock(run_id: str, lease_minutes: int = 90):
    now = utc_iso()
    lock_until = utc_iso(utc_now() + timedelta(minutes=lease_minutes))
    result = db.query(
        """
        MERGE (s:SyncState {name: $name})
        WITH s
        WHERE s.lock_until IS NULL OR s.lock_until < $now OR s.lock_run_id = $run_id
        SET s.lock_run_id = $run_id,
            s.lock_until = $lock_until,
            s.status = 'running',
            s.updated_at = $now
        RETURN s.name AS name
        """,
        {"name": SYNC_NAME, "run_id": run_id, "now": now, "lock_until": lock_until},
    )
    if not result_single(result):
        raise RuntimeError("Another Exxas sync run is already active.")


def start_run(run_id: str, watermark_before: str):
    db.write(
        """
        MERGE (r:SyncRun {id: $run_id})
        SET r.source = $source,
            r.started_at = $started_at,
            r.status = 'running',
            r.watermark_before = $watermark_before
        """,
        {
            "run_id": run_id,
            "source": SYNC_NAME,
            "started_at": utc_iso(),
            "watermark_before": watermark_before,
        },
    )


def initialize_watermark(watermark: str) -> dict[str, Any]:
    parsed = parse_erp_ts(watermark)
    if not parsed:
        raise RuntimeError(
            f"Invalid watermark {watermark!r}. Use 'YYYY-MM-DD HH:MM:SS' or ISO datetime."
        )
    normalized = format_erp_ts(parsed)
    now = utc_iso()
    db.write(
        """
        MERGE (s:SyncState {name: $name})
        SET s.watermark = $watermark,
            s.status = 'initialized',
            s.last_success_at = coalesce(s.last_success_at, $now),
            s.lock_run_id = '',
            s.lock_until = '',
            s.updated_at = $now
        """,
        {"name": SYNC_NAME, "watermark": normalized, "now": now},
    )
    return {"name": SYNC_NAME, "watermark": normalized, "status": "initialized"}


def finish_run(
    run_id: str,
    status: str,
    watermark_before: str,
    watermark_after: str,
    api_calls: int,
    counts: dict[str, Any],
    error: str | None = None,
):
    finished_at = utc_iso()
    db.write(
        """
        MERGE (r:SyncRun {id: $run_id})
        SET r.source = $source,
            r.finished_at = $finished_at,
            r.status = $status,
            r.watermark_before = $watermark_before,
            r.watermark_after = $watermark_after,
            r.api_calls = $api_calls,
            r.counts_json = $counts_json,
            r.error = $error
        """,
        {
            "run_id": run_id,
            "source": SYNC_NAME,
            "finished_at": finished_at,
            "status": status,
            "watermark_before": watermark_before,
            "watermark_after": watermark_after,
            "api_calls": api_calls,
            "counts_json": json.dumps(counts, sort_keys=True),
            "error": error,
        },
    )
    if status == "success":
        db.write(
            """
            MERGE (s:SyncState {name: $name})
            SET s.watermark = $watermark_after,
                s.last_success_at = $finished_at,
                s.last_run_id = $run_id,
                s.status = 'success',
                s.lock_run_id = '',
                s.lock_until = '',
                s.updated_at = $finished_at
            """,
            {
                "name": SYNC_NAME,
                "watermark_after": watermark_after,
                "finished_at": finished_at,
                "run_id": run_id,
            },
        )
    else:
        db.write(
            """
            MERGE (s:SyncState {name: $name})
            SET s.last_run_id = $run_id,
                s.status = $status,
                s.lock_run_id = '',
                s.lock_until = '',
                s.updated_at = $finished_at
            """,
            {"name": SYNC_NAME, "run_id": run_id, "status": status, "finished_at": finished_at},
        )


def build_sync_payload(client: ExxasClient, since: str, counts: SyncCounts) -> tuple[dict[str, Any], str]:
    service_filter = '{propertyFilter:{property:typ,operator:equals,filterValue:"s"}}'
    changed_docs = fetch_changed(
        client, "Dokument", SERVICE_DOC_FIELDS, "editDate", since, service_filter
    )
    changed_comments = fetch_changed(client, "Kommentar", COMMENT_FIELDS, "datum", since)
    changed_machines = fetch_changed(client, "Produkt", MACHINE_FIELDS, "editDate", since)

    counts.changed_docs_seen = len(changed_docs)
    counts.changed_comments_seen = len(changed_comments)
    counts.changed_machines_seen = len(changed_machines)

    docs_by_id = {str(d.get("id")): d for d in changed_docs if d.get("id")}
    for comment in changed_comments:
        if str(comment.get("refTyp") or "").lower() != "dok":
            continue
        doc_id = str(comment.get("refId") or "").strip()
        if not doc_id or doc_id in docs_by_id:
            continue
        doc = fetch_service_doc_by_id(client, doc_id)
        if doc and doc.get("typ", "s") == "s":
            docs_by_id[doc_id] = doc

    machines_by_id = {str(m.get("id")): m for m in changed_machines if m.get("id")}
    docs_by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    watermark_candidates: list[str | None] = []

    for doc in docs_by_id.values():
        watermark_candidates.append(doc.get("editDate"))
        ref_product = doc.get("refProdukt") or {}
        machine_id = str(ref_product.get("id") or "").strip()
        if not machine_id:
            counts.reject("service_doc_missing_machine")
            continue
        machine = machines_by_id.get(machine_id) or fetch_machine_by_id(client, machine_id)
        if not machine:
            counts.reject("machine_not_found")
            continue
        machines_by_id[machine_id] = machine
        comments = fetch_comments_for_doc(client, str(doc["id"]))
        parts = fetch_parts_for_doc(client, str(doc["id"]))
        watermark_candidates.extend(c.get("datum") for c in comments)
        docs_by_machine[machine_id].append(
            {"service_document": doc, "comments": comments, "parts": parts}
        )

    machines_out = []
    for machine_id, machine in sorted(machines_by_id.items()):
        watermark_candidates.append(machine.get("editDate"))
        machines_out.append(
            {
                "machine_id": machine_id,
                "machine": machine,
                "service_documents": docs_by_machine.get(machine_id, []),
            }
        )

    payload = {
        "created_at_utc": utc_iso(),
        "source": "exxas_daily_graphql",
        "watermark_query_since": since,
        "summary": {
            "machines_in_payload": len(machines_out),
            "service_docs_in_payload": sum(len(m["service_documents"]) for m in machines_out),
            "api_calls_used": client.call_count,
        },
        "machines": machines_out,
    }
    watermark_after = max_erp_ts(watermark_candidates) or since
    return payload, watermark_after


def import_payload(payload: dict[str, Any], counts: SyncCounts, dry_run: bool = False):
    for machine_record in payload.get("machines") or []:
        machine = machine_record.get("machine") or {}
        machine_id = str(machine.get("id") or machine_record.get("machine_id") or "").strip()
        if not machine_id:
            counts.reject("payload_machine_missing_id")
            continue
        if not dry_run:
            upsert_machine(machine)
        counts.machines_touched += 1

        for service_record in machine_record.get("service_documents") or []:
            doc = service_record.get("service_document") or {}
            doc_id = str(doc.get("id") or "").strip()
            if not doc_id:
                counts.reject("payload_doc_missing_id")
                continue
            if not dry_run:
                upsert_service_job(machine_id, doc)
            counts.service_docs_touched += 1

            for comment in service_record.get("comments") or []:
                if not dry_run:
                    upsert_comment(doc_id, comment)
                counts.comments_touched += 1

            for part in service_record.get("parts") or []:
                if not dry_run:
                    upsert_part_and_edge(doc_id, part)
                counts.parts_rows_touched += 1


def write_raw_report(raw_dir: str, run_id: str, report: dict[str, Any]) -> str:
    today = utc_now()
    out_dir = Path(raw_dir) / f"{today:%Y}" / f"{today:%m}" / f"{today:%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily Exxas GraphQL sync into FalkorDB.")
    parser.add_argument("--mode", choices=["daily", "init-watermark"], default="daily")
    parser.add_argument(
        "--init-watermark",
        help="Watermark to store when --mode init-watermark is used.",
    )
    parser.add_argument("--base-url", default=os.environ.get("EXXAS_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--user", default=os.environ.get("EXXAS_USER"))
    parser.add_argument("--password", default=os.environ.get("EXXAS_PASSWORD"))
    parser.add_argument("--max-calls", type=int, default=450)
    parser.add_argument("--min-call-interval-ms", type=int, default=900)
    parser.add_argument("--lookback-hours", type=int, default=48)
    parser.add_argument("--raw-dir", default="/data/exxas/raw")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db.connect()

    if args.mode == "init-watermark":
        if not args.init_watermark:
            raise SystemExit("--init-watermark is required when --mode init-watermark is used.")
        state = initialize_watermark(args.init_watermark)
        print(json.dumps(state, indent=2))
        return 0

    user = (args.user or "").strip()
    password = args.password or ""
    if not user or not password:
        raise SystemExit("Missing Exxas credentials. Set EXXAS_USER and EXXAS_PASSWORD.")

    run_id = str(uuid.uuid4())
    counts = SyncCounts()
    client = ExxasClient(
        base_url=args.base_url,
        user=user,
        password=password,
        min_interval_ms=args.min_call_interval_ms,
        max_calls=args.max_calls,
    )
    watermark_before = get_state_watermark() or derive_initial_watermark()
    if not watermark_before:
        raise SystemExit(
            "No Exxas sync watermark found. Bootstrap/import new ERP data before running daily sync."
        )
    since = effective_watermark(watermark_before, args.lookback_hours)

    if not args.dry_run:
        acquire_lock(run_id)
        start_run(run_id, watermark_before)

    try:
        client.login()
        payload, watermark_after = build_sync_payload(client, since, counts)
        import_payload(payload, counts, dry_run=args.dry_run)
        report = {
            "run_id": run_id,
            "source": SYNC_NAME,
            "status": "success",
            "dry_run": args.dry_run,
            "watermark_before": watermark_before,
            "watermark_query_since": since,
            "watermark_after": watermark_after,
            "api_calls": client.call_count,
            "counts": counts.as_dict(),
            "payload": payload,
        }
        raw_path = write_raw_report(args.raw_dir, run_id, report)
        report["raw_report_path"] = raw_path
        if not args.dry_run:
            finish_run(
                run_id,
                "success",
                watermark_before,
                watermark_after,
                client.call_count,
                counts.as_dict(),
            )
        print(json.dumps({k: v for k, v in report.items() if k != "payload"}, indent=2))
        return 0
    except Exception as exc:
        error = str(exc)
        report = {
            "run_id": run_id,
            "source": SYNC_NAME,
            "status": "failed",
            "dry_run": args.dry_run,
            "watermark_before": watermark_before,
            "watermark_query_since": since,
            "api_calls": client.call_count,
            "counts": counts.as_dict(),
            "error": error,
        }
        write_raw_report(args.raw_dir, run_id, report)
        if not args.dry_run:
            finish_run(
                run_id,
                "failed",
                watermark_before,
                watermark_before,
                client.call_count,
                counts.as_dict(),
                error=error,
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
