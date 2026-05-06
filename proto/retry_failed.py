"""Clear failed image items from checkpoint and re-run ingest to retry them."""

import json
from pathlib import Path

from proto import PROTO_CACHE_DIR

CHECKPOINT = Path(PROTO_CACHE_DIR) / "ingest_checkpoint.json"


def main():
    cp = json.loads(CHECKPOINT.read_text())
    cleared = 0
    for slug, items in cp["done"].items():
        for key in list(items.keys()):
            entry = items[key]
            if isinstance(entry, dict) and entry.get("err"):
                del items[key]
                cleared += 1
            elif isinstance(entry, dict) and entry.get("ok") == 0 and key.startswith("img::"):
                del items[key]
                cleared += 1
    CHECKPOINT.write_text(json.dumps(cp, indent=2))
    print(f"Cleared {cleared} failed items from checkpoint.")


if __name__ == "__main__":
    main()
