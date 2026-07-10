from __future__ import annotations

import unittest
from unittest import mock

from proto import answer
from proto import erp_context


class ProtoErpContextTests(unittest.TestCase):
    def test_chat_answer_includes_erp_context_without_document_hits(self):
        captured = {}
        erp = {
            "machine": {
                "erp_id": "157",
                "title": "Falzmaschine M9 / R80 404406",
                "serial_number": "10 10 85 23 10 01",
                "customer": "Birkhäuser + GBC AG",
                "machine_type": "Falzmaschine",
                "brand": "MBO",
            },
            "link": {
                "erp_match_confidence": 1.0,
                "erp_match_method": "exact_identifier",
            },
            "last_service_date": "2026-06-01",
            "frequent_parts": [{"nummer": "A-1", "titel": "Sensor", "usage_count": 3}],
            "recent_jobs": [{"date": "2026-06-01", "nummer": "S1", "title": "Service", "comments": []}],
        }

        def fake_chat_messages(messages, **kwargs):
            captured["messages"] = messages
            return "ERP answer"

        with mock.patch.object(answer, "retrieve", return_value=[]), \
            mock.patch.object(answer, "retrieve_erp_context", return_value=erp), \
            mock.patch.object(answer, "vision_chat_messages", side_effect=fake_chat_messages):
            result = answer.chat_answer(
                "Welche Teile wurden oft benutzt?",
                transcript=[],
                memories=[],
                machine_slug="falzmaschine-m9-404406",
            )

        self.assertEqual(result["answer"], "ERP answer")
        self.assertEqual(result["erp_context"], erp)
        text_part = captured["messages"][1]["content"][0]["text"]
        self.assertIn("LINKED ERP CONTEXT", text_part)
        self.assertIn("ERP MACHINE RECORD", text_part)
        self.assertIn("A-1", text_part)
        self.assertIn("Do not offer to create/send PDFs", captured["messages"][1]["content"][-1]["text"])

    def test_chat_answer_without_hits_memory_or_confident_erp_returns_empty_message(self):
        with mock.patch.object(answer, "retrieve", return_value=[]), \
            mock.patch.object(answer, "retrieve_erp_context", return_value=None), \
            mock.patch.object(answer, "vision_chat_messages") as chat:
            result = answer.chat_answer(
                "Question",
                transcript=[],
                memories=[],
                machine_slug="low-confidence",
            )

        self.assertIn("Brak wyników", result["answer"])
        self.assertIsNone(result["erp_context"])
        chat.assert_not_called()

    def test_get_proto_erp_link_rejects_low_confidence(self):
        fake_result = mock.Mock(
            header=[
                (None, "machine_slug"),
                (None, "proto_folder"),
                (None, "erp_id"),
                (None, "erp_customer_id"),
                (None, "erp_match_method"),
                (None, "erp_match_confidence"),
            ],
            result_set=[["machine-a", "Machine A", "erp-1", "cust-1", "customer_title_similarity", 0.55]],
        )
        with mock.patch.object(erp_context.proto_db, "query", return_value=fake_result):
            self.assertIsNone(erp_context.get_proto_erp_link("machine-a", min_confidence=0.78))

    def test_format_erp_context_labels_group_records(self):
        text = erp_context.format_erp_context({
            "link": {
                "erp_link_mode": "group",
                "erp_group_identifier": "404406",
                "erp_match_confidence": 1.0,
                "erp_match_method": "exact_identifier_group",
            },
            "erp_ids": ["156", "157"],
            "machines": [
                {"erp_id": "156", "title": "Falzanlage M9 404406", "machine_type": "Falzanlage", "brand": "MBO"},
                {"erp_id": "157", "title": "Falzmaschine M9 / R80 404406", "machine_type": "Falzmaschine", "brand": "MBO"},
            ],
            "frequent_parts": [{"nummer": "A-1", "titel": "Sensor", "usage_count": 2}],
            "recent_jobs": [
                {"date": "2026-06-01", "nummer": "S1", "title": "Service", "machine_erp_id": "157", "comments": []}
            ],
            "last_service_date": "2026-06-01",
        })

        self.assertIn("ERP RELATED MACHINE/LINE RECORDS", text)
        self.assertIn("ERP record count: 2", text)
        self.assertIn("Representative ERP IDs: 156, 157", text)
        self.assertIn("treat these records as one related machine/line", text)
        self.assertIn("@ 157", text)


if __name__ == "__main__":
    unittest.main()
