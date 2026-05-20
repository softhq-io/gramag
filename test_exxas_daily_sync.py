from __future__ import annotations

import unittest
from unittest import mock

import exxas_daily_sync as sync


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def post(self, *args, **kwargs):
        if not self.payloads:
            raise AssertionError("Unexpected POST")
        return FakeResponse(self.payloads.pop(0))


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def query(self, query, variables=None):
        variables = variables or {}
        self.calls.append(variables)
        offset = variables.get("offset", 0)
        return {"Dokument": self.pages.get(offset, [])}


class ExxasDailySyncTests(unittest.TestCase):
    def test_fetch_pages_stops_after_short_page(self):
        client = FakeClient(
            {
                0: [{"id": "1"}, {"id": "2"}],
                2: [{"id": "3"}],
            }
        )

        rows = sync.fetch_pages(client, "Dokument", "query", page_size=2)

        self.assertEqual([r["id"] for r in rows], ["1", "2", "3"])
        self.assertEqual([c["offset"] for c in client.calls], [0, 2])

    def test_query_retries_after_rate_limit(self):
        session = FakeSession(
            [
                {"errors": [{"message": "too many requests"}]},
                {"data": {"Dokument": [{"id": "1"}]}},
            ]
        )
        client = sync.ExxasClient(
            "https://api.exxas.net",
            "user",
            "password",
            min_interval_ms=0,
            max_calls=5,
            session=session,
        )
        client.graphql_url = "https://graphql.example"
        client.headers = {"Authorization": "Bearer token"}

        with mock.patch.object(sync.time, "sleep"):
            data = client.query("query")

        self.assertEqual(data["Dokument"][0]["id"], "1")
        self.assertEqual(client.call_count, 2)

    def test_effective_watermark_uses_lookback(self):
        self.assertEqual(
            sync.effective_watermark("2026-04-22 16:40:43", 48),
            "2026-04-20 16:40:43",
        )

    def test_build_payload_maps_changed_docs_comments_and_machines(self):
        counts = sync.SyncCounts()

        def fake_fetch_changed(client, root, fields, timestamp_field, since, extra_filters="", page_size=200):
            if root == "Dokument":
                return [
                    {
                        "id": "d1",
                        "editDate": "2026-04-22 16:40:43",
                        "refProdukt": {"id": "m1"},
                        "refKunde": {"id": "c1", "nummer": "100"},
                    }
                ]
            if root == "Kommentar":
                return [
                    {
                        "id": "cmt1",
                        "datum": "2026-04-22 17:00:00",
                        "refTyp": "dok",
                        "refId": "d2",
                    }
                ]
            if root == "Produkt":
                return [{"id": "m3", "editDate": "2026-04-22 18:00:00"}]
            raise AssertionError(root)

        with mock.patch.object(sync, "fetch_changed", side_effect=fake_fetch_changed), \
            mock.patch.object(sync, "fetch_service_doc_by_id") as by_id, \
            mock.patch.object(sync, "fetch_machine_by_id") as machine_by_id, \
            mock.patch.object(sync, "fetch_comments_for_doc") as comments, \
            mock.patch.object(sync, "fetch_parts_for_doc") as parts:
            by_id.return_value = {
                "id": "d2",
                "editDate": "2026-04-22 17:05:00",
                "refProdukt": {"id": "m2"},
                "refKunde": {"id": "c2", "nummer": "200"},
            }
            machine_by_id.side_effect = lambda client, machine_id: {
                "id": machine_id,
                "editDate": "2026-04-22 17:10:00",
            }
            comments.return_value = [{"id": "x", "datum": "2026-04-22 17:15:00"}]
            parts.return_value = [{"id": "p"}]

            payload, watermark = sync.build_sync_payload(mock.Mock(call_count=0), "2026-04-20 00:00:00", counts)

        self.assertEqual(counts.changed_docs_seen, 1)
        self.assertEqual(counts.changed_comments_seen, 1)
        self.assertEqual(counts.changed_machines_seen, 1)
        self.assertEqual(watermark, "2026-04-22 18:00:00")
        self.assertEqual([m["machine_id"] for m in payload["machines"]], ["m1", "m2", "m3"])
        self.assertEqual(sum(len(m["service_documents"]) for m in payload["machines"]), 2)

    def test_import_payload_is_repeatable_for_same_ids(self):
        payload = {
            "machines": [
                {
                    "machine_id": "m1",
                    "machine": {"id": "m1"},
                    "service_documents": [
                        {
                            "service_document": {"id": "d1"},
                            "comments": [{"id": "c1"}],
                            "parts": [{"id": "p1"}],
                        }
                    ],
                }
            ]
        }

        calls = []
        with mock.patch.object(sync, "upsert_machine", side_effect=lambda m: calls.append(("m", m["id"]))), \
            mock.patch.object(sync, "upsert_service_job", side_effect=lambda m, d: calls.append(("d", d["id"]))), \
            mock.patch.object(sync, "upsert_comment", side_effect=lambda d, c: calls.append(("c", c["id"]))), \
            mock.patch.object(sync, "upsert_part_and_edge", side_effect=lambda d, p: calls.append(("p", p["id"]))):
            first = sync.SyncCounts()
            second = sync.SyncCounts()
            sync.import_payload(payload, first)
            sync.import_payload(payload, second)

        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(
            calls,
            [("m", "m1"), ("d", "d1"), ("c", "c1"), ("p", "p1")] * 2,
        )


if __name__ == "__main__":
    unittest.main()
