#!/bin/bash
# Azure entrypoint: waits for external FalkorDB, seeds if needed, starts uvicorn.
set -euo pipefail

mkdir -p /data/source /data/cache

echo "[entrypoint] waiting for FalkorDB at ${FALKORDB_HOST}:${FALKORDB_PORT:-6379}..."
python - <<'PYEOF'
import os, sys, time
from falkordb import FalkorDB

host = os.getenv("FALKORDB_HOST", "falkordb")
port = int(os.getenv("FALKORDB_PORT", 6379))

for i in range(60):
    try:
        db = FalkorDB(host=host, port=port)
        db.connection.ping()
        print("[entrypoint] FalkorDB ready")
        sys.exit(0)
    except Exception as e:
        if i == 59:
            print(f"[entrypoint] FalkorDB did not become ready: {e}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PYEOF

# Seed ERP data (idempotent — skips if already populated)
cd /app
ERP_SEED_FILE=${ERP_SEED_FILE:-/app/data/demo_subset.json}
if [ -f "$ERP_SEED_FILE" ]; then
  EXISTING=$(python -c "
from db import db
db.connect()
print(db.node_count('Machine'))
" 2>/dev/null || echo "0")

  if [ "$EXISTING" = "0" ]; then
    echo "[entrypoint] seeding ERP schema + indexes..."
    python -c "from schema import apply_indexes; apply_indexes()" || true

    echo "[entrypoint] importing ERP demo subset..."
    python import_new_erp_subset.py --input "$ERP_SEED_FILE"

    echo "[entrypoint] seeding users..."
    python -c "from seed_users import seed; seed()"
  else
    echo "[entrypoint] ERP graph already has $EXISTING machines — skipping seed"
  fi
else
  echo "[entrypoint] no ERP seed file at $ERP_SEED_FILE — skipping"
fi

echo "[entrypoint] starting uvicorn"
exec python -m uvicorn proto_server:app --host 0.0.0.0 --port 8000
