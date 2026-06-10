from __future__ import annotations

import unittest

from proto.shards import plan_shards


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


if __name__ == "__main__":
    unittest.main()
