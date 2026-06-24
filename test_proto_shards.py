from __future__ import annotations

import unittest

from proto.shards import plan_shards, render_terraform_shards


class ProtoShardPlannerTests(unittest.TestCase):
    def test_plan_shards_maps_each_machine_once(self):
        manifest = {
            "machines": [
                {
                    "folder": "Machine A",
                    "rel_path": "Customer 1/Machine A",
                    "slug": "customer-1-machine-a",
                    "files": {"pdf": [{"path": "a.pdf"}], "image": [], "text": []},
                },
                {
                    "folder": "Machine B",
                    "rel_path": "Customer 1/Machine B",
                    "slug": "customer-1-machine-b",
                    "files": {"pdf": [{"path": "b.pdf"}, {"path": "c.pdf"}], "image": [], "text": []},
                },
                {
                    "folder": "Machine C",
                    "rel_path": "Customer 2/Machine C",
                    "slug": "customer-2-machine-c",
                    "files": {"pdf": [], "image": [{"path": "c.jpg"}], "text": [{"path": "c.txt"}]},
                },
            ]
        }

        plan = plan_shards(manifest, shard_count=2)
        include_paths = [
            path
            for shard in plan["shards"]
            for path in shard["include_paths"]
        ]

        self.assertEqual(sorted(include_paths), [
            "Customer 1/Machine A",
            "Customer 1/Machine B",
            "Customer 2/Machine C",
        ])
        self.assertEqual(len(include_paths), len(set(include_paths)))
        self.assertEqual(plan["summary"]["machine_count"], 3)
        self.assertEqual(plan["summary"]["pdfs"], 3)

    def test_render_terraform_shards_creates_isolated_pdf_image_and_import_jobs(self):
        manifest = {
            "machines": [
                {
                    "folder": f"Machine {i}",
                    "rel_path": f"Customer {i % 2}/Machine {i}",
                    "slug": f"customer-machine-{i}",
                    "files": {"pdf": [{"path": f"{i}.pdf"}], "image": [{"path": f"{i}.jpg"}], "text": []},
                }
                for i in range(1, 5)
            ]
        }

        plan = plan_shards(manifest, shard_count=2)
        tfvars = render_terraform_shards(plan, name_prefix="clients-a", stage_prefix="clients-a")

        self.assertIn("sharepoint_proto_ingest_shards = {", tfvars)
        self.assertIn("clients-a01-pdf = {", tfvars)
        self.assertIn("clients-a01-img = {", tfvars)
        self.assertIn("clients-a02-pdf = {", tfvars)
        self.assertIn("clients-a02-img = {", tfvars)
        self.assertIn("clients-a-import-pdf = {", tfvars)
        self.assertIn("clients-a-import-img = {", tfvars)
        self.assertIn('ingest_stage_output_dir = "/data/proto-stage/clients-a-pdf"', tfvars)
        self.assertIn('ingest_stage_output_dir = "/data/proto-stage/clients-a-image"', tfvars)
        self.assertIn('ingest_import_checkpoint = "/data/proto-stage/clients-a-pdf/import_checkpoint.json"', tfvars)
        self.assertIn('skip_mirror = true', tfvars)

        pdf_assignments = tfvars.count('ingest_kinds = "pdf,text"') - 1  # exclude import job
        image_assignments = tfvars.count('ingest_kinds = "image"') - 1  # exclude import job
        self.assertEqual(pdf_assignments, 2)
        self.assertEqual(image_assignments, 2)


if __name__ == "__main__":
    unittest.main()
