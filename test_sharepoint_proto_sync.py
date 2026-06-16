from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import sharepoint_proto_sync as sync
from proto.scan import build_manifest


class FakeGraph:
    def __init__(self):
        self.json = {}
        self.bytes = {}

    def request_json(self, path):
        value = self.json.get(path)
        if value is None:
            raise AssertionError(f"Unexpected JSON request: {path}")
        return value

    def request_bytes(self, path):
        value = self.bytes.get(path)
        if value is None:
            raise AssertionError(f"Unexpected bytes request: {path}")
        return value


class SharePointProtoSyncTests(unittest.TestCase):
    def test_parse_sharepoint_folder_url(self):
        parts = sync.parse_sharepoint_web_url(
            "https://gramagch.sharepoint.com/sites/Services/"
            "Freigegebene%20Dokumente/Forms/AllItems.aspx"
            "?id=%2Fsites%2FServices%2FFreigegebene%20Dokumente"
            "%2FKundendienst%2FKunden%2FBodan%20AG"
        )

        self.assertEqual(parts.hostname, "gramagch.sharepoint.com")
        self.assertEqual(parts.site_path, "/sites/Services")
        self.assertEqual(parts.drive_name, "Freigegebene Dokumente")
        self.assertEqual(parts.root_path, "Kundendienst/Kunden/Bodan AG")

    def test_relative_path_strips_configured_root(self):
        item = {
            "name": "manual.pdf",
            "parentReference": {
                "path": "/drives/drive-id/root:/Machines/Folder A/Docs",
            },
        }

        self.assertEqual(
            sync._relative_path_from_parent(item, "Machines"),
            "Folder A/Docs/manual.pdf",
        )

    def test_mirror_delta_downloads_changed_file(self):
        graph = FakeGraph()
        graph.json["/drives/drive/root/delta"] = {
            "value": [
                {
                    "id": "item1",
                    "name": "manual.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "size": 7,
                    "eTag": "v1",
                    "lastModifiedDateTime": "2026-05-20T10:00:00Z",
                    "parentReference": {"path": "/drives/drive/root:/Machine A"},
                }
            ],
            "@odata.deltaLink": "https://delta.example/next",
        }
        graph.bytes["/drives/drive/items/item1/content"] = b"content"

        with tempfile.TemporaryDirectory() as tmp:
            state = sync.SyncState()
            counts = sync.mirror_delta(
                graph,
                "drive",
                None,
                "",
                state,
                Path(tmp),
                {".pdf"},
                full=False,
            )

            self.assertEqual(counts["downloaded"], 1)
            self.assertEqual((Path(tmp) / "Machine A/manual.pdf").read_bytes(), b"content")
            self.assertEqual(state.delta_link, "https://delta.example/next")
            self.assertEqual(state.items["item1"]["rel_path"], "Machine A/manual.pdf")

    def test_mirror_delta_respects_max_downloads(self):
        graph = FakeGraph()
        graph.json["/drives/drive/root/delta"] = {
            "value": [
                {
                    "id": "item1",
                    "name": "a.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "size": 1,
                    "eTag": "v1",
                    "parentReference": {"path": "/drives/drive/root:/Machine A"},
                },
                {
                    "id": "item2",
                    "name": "b.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "size": 1,
                    "eTag": "v1",
                    "parentReference": {"path": "/drives/drive/root:/Machine A"},
                },
            ],
            "@odata.deltaLink": "https://delta.example/next",
        }
        graph.bytes["/drives/drive/items/item1/content"] = b"a"

        with tempfile.TemporaryDirectory() as tmp:
            counts = sync.mirror_delta(
                graph,
                "drive",
                None,
                "",
                sync.SyncState(),
                Path(tmp),
                {".pdf"},
                full=False,
                max_downloads=1,
            )

            self.assertEqual(counts["downloaded"], 1)
            self.assertEqual(counts["skipped"], 1)
            self.assertTrue((Path(tmp) / "Machine A/a.pdf").exists())
            self.assertFalse((Path(tmp) / "Machine A/b.pdf").exists())

    def test_mirror_delta_respects_include_paths(self):
        graph = FakeGraph()
        graph.json["/drives/drive/root/delta"] = {
            "value": [
                {
                    "id": "item1",
                    "name": "a.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "size": 1,
                    "eTag": "v1",
                    "parentReference": {"path": "/drives/drive/root:/Kunden/Included/Machine A"},
                },
                {
                    "id": "item2",
                    "name": "b.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "size": 1,
                    "eTag": "v1",
                    "parentReference": {"path": "/drives/drive/root:/Kunden/Other/Machine B"},
                },
            ],
            "@odata.deltaLink": "https://delta.example/next",
        }
        graph.bytes["/drives/drive/items/item1/content"] = b"a"

        with tempfile.TemporaryDirectory() as tmp:
            state = sync.SyncState()
            counts = sync.mirror_delta(
                graph,
                "drive",
                None,
                "Kunden",
                state,
                Path(tmp),
                {".pdf"},
                full=False,
                include_paths=["Included"],
            )

            self.assertEqual(counts["downloaded"], 1)
            self.assertEqual(counts["skipped"], 1)
            self.assertEqual(state.include_paths, ["Included"])
            self.assertTrue((Path(tmp) / "Included/Machine A/a.pdf").exists())
            self.assertFalse((Path(tmp) / "Other/Machine B/b.pdf").exists())

    def test_ingest_log_path_uses_durable_log_dir_and_safe_names(self):
        original = {
            "PROTO_INGEST_LOG_DIR": os.environ.get("PROTO_INGEST_LOG_DIR"),
            "CONTAINER_APP_JOB_NAME": os.environ.get("CONTAINER_APP_JOB_NAME"),
            "CONTAINER_APP_JOB_EXECUTION_NAME": os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME"),
        }
        try:
            os.environ["PROTO_INGEST_LOG_DIR"] = "/data/ingest-logs"
            os.environ["CONTAINER_APP_JOB_NAME"] = "staging/sp proto"
            os.environ["CONTAINER_APP_JOB_EXECUTION_NAME"] = "exec:123"

            self.assertEqual(
                sync.ingest_log_path(),
                Path("/data/ingest-logs/staging-sp-proto/exec-123.log"),
            )
        finally:
            for name, value in original.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_parse_include_paths_accepts_newline_shard_lists(self):
        self.assertEqual(
            sync.parse_include_paths("Customer/Machine B\nCustomer/Machine A, Customer/Machine A"),
            ["Customer/Machine A", "Customer/Machine B"],
        )

    def test_run_ingest_passes_kind_controls(self):
        calls = []
        original_run = sync.run_logged_subprocess
        try:
            sync.run_logged_subprocess = lambda command, log_path: calls.append((command, log_path))

            sync.run_ingest(
                Namespace(
                    apply_schema=False,
                    ingest_all=True,
                    ingest_force=False,
                    ingest_workers=2,
                    ingest_img_workers=1,
                    ingest_machine_workers=1,
                    ingest_kinds="pdf,text",
                    ingest_stage_output_dir=None,
                    ingest_import_output_dir=None,
                    ingest_import_checkpoint=None,
                    ingest_import_sleep=0,
                    ingest_arg=[],
                )
            )

            command, log_path = calls[0]
            self.assertTrue(str(log_path).endswith(".log"))
            self.assertIn("--kinds", command)
            self.assertEqual(command[command.index("--kinds") + 1], "pdf,text")
        finally:
            sync.run_logged_subprocess = original_run

    def test_run_ingest_passes_stage_import_controls(self):
        calls = []
        original_run = sync.run_logged_subprocess
        try:
            sync.run_logged_subprocess = lambda command, log_path: calls.append((command, log_path))

            sync.run_ingest(
                Namespace(
                    apply_schema=False,
                    ingest_all=False,
                    ingest_force=False,
                    ingest_workers=1,
                    ingest_img_workers=1,
                    ingest_machine_workers=1,
                    ingest_kinds="pdf,text",
                    ingest_stage_output_dir="/data/proto-stage/pdf",
                    ingest_import_output_dir="/data/proto-stage/pdf",
                    ingest_import_checkpoint="/data/proto-stage/pdf/import.json",
                    ingest_import_sleep=0.05,
                    ingest_arg=[],
                )
            )

            command, _log_path = calls[0]
            self.assertEqual(command[command.index("--stage-output-dir") + 1], "/data/proto-stage/pdf")
            self.assertEqual(command[command.index("--import-output-dir") + 1], "/data/proto-stage/pdf")
            self.assertEqual(command[command.index("--import-checkpoint") + 1], "/data/proto-stage/pdf/import.json")
            self.assertEqual(command[command.index("--import-sleep") + 1], "0.05")
        finally:
            sync.run_logged_subprocess = original_run

    def test_skip_mirror_runs_ingest_without_graph_client(self):
        calls = []
        original_argv = sync.sys.argv
        original_run = sync.run_ingest
        original_client = sync.GraphClient.from_client_credentials
        try:
            sync.sys.argv = ["sharepoint_proto_sync.py", "--skip-mirror", "--run-ingest"]
            sync.run_ingest = lambda args: calls.append(args)
            sync.GraphClient.from_client_credentials = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Graph auth should be skipped")
            )

            with redirect_stdout(StringIO()):
                sync.main()

            self.assertEqual(len(calls), 1)
        finally:
            sync.sys.argv = original_argv
            sync.run_ingest = original_run
            sync.GraphClient.from_client_credentials = original_client

    def test_safe_target_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                sync.safe_target(Path(tmp), "../outside.pdf")

    def test_machine_name_score_matches_proto_folder_style(self):
        self.assertTrue(sync.machine_name_score("Falzmaschine   T800-6-R   Nr 59 99 03 04"))
        self.assertFalse(sync.machine_name_score("Bodan AG"))

    def test_customer_root_mode_scans_customer_then_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = (
                Path(tmp)
                / "Bodan AG"
                / "Folieranlage   Pratica   401540"
                / "Manuals"
                / "manual.pdf"
            )
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.4")

            manifest = build_manifest(tmp, root_mode="customers")

            self.assertEqual(manifest["summary"]["machine_count"], 1)
            machine = manifest["machines"][0]
            self.assertEqual(machine["customer"], "Bodan AG")
            self.assertEqual(machine["folder"], "Folieranlage   Pratica   401540")
            self.assertEqual(machine["slug"], "bodan-ag-folieranlage-pratica-401540")

    def test_machine_root_mode_can_attach_customer_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "Folieranlage   Pratica   401540" / "manual.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.4")

            manifest = build_manifest(tmp, root_mode="machines", customer_name="Bodan AG")

            machine = manifest["machines"][0]
            self.assertEqual(machine["customer"], "Bodan AG")
            self.assertEqual(machine["slug"], "folieranlage-pratica-401540")


if __name__ == "__main__":
    unittest.main()
