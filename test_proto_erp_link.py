from __future__ import annotations

import unittest
from unittest import mock

import proto_erp_link as link


class ProtoErpLinkTests(unittest.TestCase):
    def test_exact_serial_match(self):
        decision = link.decide_match(
            {"slug": "proto-a", "serial": "3068-030", "folder": "Schneidmaschine"},
            [
                {"erp_id": "1", "serial_number": "1111", "title": "Other"},
                {"erp_id": "2", "serial_number": "3068-030", "title": "Schneidmaschine WPS92"},
            ],
        )

        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.match.erp_id, "2")
        self.assertEqual(decision.match.method, "exact_identifier")
        self.assertEqual(decision.match.confidence, 1.0)

    def test_asset_number_match_from_folder_and_erp_title(self):
        decision = link.decide_match(
            {"slug": "falzmaschine-m9-404406", "serial": None, "folder": "Falzanlage   M9   404406"},
            [
                {
                    "erp_id": "157",
                    "serial_number": "10 10 85 23 10 01",
                    "title": "Falzmaschine   M9 / R80   404406",
                }
            ],
        )

        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.match.erp_id, "157")
        self.assertIn("404406", decision.match.reason)

    def test_group_identifier_match_when_same_customer(self):
        decision = link.decide_match(
            {"slug": "proto-a", "serial": "404406", "folder": "Falzanlage M9 404406"},
            [
                {"erp_id": "1", "serial_number": "404406", "title": "Machine A", "customer_erp_id": "c1"},
                {"erp_id": "2", "serial_number": "", "title": "Machine B 404406", "customer_erp_id": "c1"},
            ],
        )

        self.assertEqual(decision.status, "group")
        self.assertEqual(decision.group_identifier, "404406")
        self.assertEqual([c.erp_id for c in decision.candidates], ["1", "2"])

    def test_ambiguous_identifier_is_rejected_across_customers(self):
        decision = link.decide_match(
            {"slug": "proto-a", "serial": "404406", "folder": "Falzanlage M9 404406"},
            [
                {"erp_id": "1", "serial_number": "404406", "title": "Machine A", "customer_erp_id": "c1"},
                {"erp_id": "2", "serial_number": "", "title": "Machine B 404406", "customer_erp_id": "c2"},
            ],
        )

        self.assertEqual(decision.status, "ambiguous")
        self.assertIsNone(decision.match)
        self.assertEqual(len(decision.candidates), 2)

    def test_no_candidate_stays_unmatched(self):
        decision = link.decide_match(
            {"slug": "kraus", "serial": None, "folder": "Kraus Anlagen", "customer": "Birkhäuser + GBC AG"},
            [{"erp_id": "1", "title": "Falzanlage M9 404406", "customer": "Birkhäuser + GBC AG"}],
        )

        self.assertEqual(decision.status, "unmatched")
        self.assertIsNone(decision.match)

    def test_manual_override_wins(self):
        decision = link.decide_match(
            {"slug": "proto-a", "serial": "1111", "folder": "Proto Machine"},
            [{"erp_id": "2", "serial_number": "9999", "title": "ERP Machine", "customer_erp_id": "c1"}],
            overrides={"proto-a": {"erp_id": "2", "reason": "reviewed"}},
        )

        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.match.erp_id, "2")
        self.assertEqual(decision.match.erp_customer_id, "c1")
        self.assertEqual(decision.match.method, "manual_override")

    def test_run_link_writes_expected_proto_properties(self):
        calls = []

        with mock.patch.object(
            link,
            "fetch_proto_machines",
            return_value=[{"slug": "proto-a", "serial": "3068-030", "folder": "Schneidmaschine"}],
        ), mock.patch.object(
            link,
            "fetch_erp_machines",
            return_value=[
                {
                    "erp_id": "1144",
                    "serial_number": "3068-030",
                    "title": "Schneidmaschine WPS92 MCS KV2",
                    "customer_erp_id": "cust1",
                }
            ],
        ), mock.patch.object(link.proto_db, "connect"), mock.patch.object(link.erp_db, "connect"), mock.patch.object(
            link,
            "write_match",
            side_effect=lambda slug, match, linked_at: calls.append((slug, match.erp_id, match.erp_customer_id, match.method)),
        ):
            report = link.run_link()

        self.assertEqual(report["summary"], {"matched": 1, "grouped": 0, "unmatched": 0, "ambiguous": 0})
        self.assertEqual(calls, [("proto-a", "1144", "cust1", "exact_identifier")])

    def test_run_link_writes_group_properties_for_same_customer_candidates(self):
        calls = []

        with mock.patch.object(
            link,
            "fetch_proto_machines",
            return_value=[{"slug": "proto-a", "serial": "404406", "folder": "Falzanlage M9 404406"}],
        ), mock.patch.object(
            link,
            "fetch_erp_machines",
            return_value=[
                {"erp_id": "156", "serial_number": "404406", "title": "Machine A", "customer_erp_id": "c1"},
                {"erp_id": "157", "serial_number": "", "title": "Machine B 404406", "customer_erp_id": "c1"},
            ],
        ), mock.patch.object(link.proto_db, "connect"), mock.patch.object(link.erp_db, "connect"), mock.patch.object(
            link,
            "write_group",
            side_effect=lambda slug, matches, group_identifier, linked_at: calls.append(
                (slug, [m.erp_id for m in matches], group_identifier)
            ),
        ):
            report = link.run_link()

        self.assertEqual(report["summary"], {"matched": 0, "grouped": 1, "unmatched": 0, "ambiguous": 0})
        self.assertEqual(calls, [("proto-a", ["156", "157"], "404406")])


if __name__ == "__main__":
    unittest.main()
