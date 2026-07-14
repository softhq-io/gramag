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

echo "[entrypoint] configuring FalkorDB persistence..."
redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" CONFIG SET save "300 1" || true
redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" CONFIG SET stop-writes-on-bgsave-error no || true

# Seed ERP data (idempotent — skips if already populated)
cd /app
python -c "from schema import apply_indexes; apply_indexes()" || true
ERP_CSV_DIR=${ERP_CSV_DIR:-/data/erp}
ERP_SEED_FILE=${ERP_SEED_FILE:-/app/data/demo_subset.json}
ERP_CSV_MARKER=${ERP_CSV_MARKER:-/data/.erp_csv_seed_fingerprint}

csv_fingerprint() {
  python - "$ERP_CSV_DIR" <<'PYEOF'
import hashlib
import os
import sys

root = sys.argv[1]
required = [
    "kunden.csv",
    "produkte.csv",
    "artikel.csv",
    "dokumente.csv",
    "dok_artikel.csv",
    "kommentare.csv",
    "adressen.csv",
]

h = hashlib.sha256()
for name in required:
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        continue
    stat = os.stat(path)
    h.update(name.encode())
    h.update(str(stat.st_size).encode())
    h.update(str(int(stat.st_mtime)).encode())
print(h.hexdigest())
PYEOF
}

EXISTING=$(python -c "
from db import db
db.connect()
print(db.node_count('Machine'))
" 2>/dev/null || echo "0")

if [ -d "$ERP_CSV_DIR" ] && [ -f "$ERP_CSV_DIR/kunden.csv" ]; then
  CURRENT_CSV_FINGERPRINT=$(csv_fingerprint)
  LAST_CSV_FINGERPRINT=$(cat "$ERP_CSV_MARKER" 2>/dev/null || true)

  if [ "$CURRENT_CSV_FINGERPRINT" != "$LAST_CSV_FINGERPRINT" ] || [ "$EXISTING" = "0" ]; then
    echo "[entrypoint] importing CSV export from $ERP_CSV_DIR..."
    python -c "from schema import apply_indexes; apply_indexes()" || true
    python validate_erp_import.py --data-dir "$ERP_CSV_DIR"
    python seed_erp.py --data-dir "$ERP_CSV_DIR"
    printf "%s" "$CURRENT_CSV_FINGERPRINT" > "$ERP_CSV_MARKER"
  else
    echo "[entrypoint] CSV export already imported for fingerprint $CURRENT_CSV_FINGERPRINT — skipping"
  fi
elif [ "$EXISTING" = "0" ]; then
  echo "[entrypoint] seeding ERP schema + indexes..."
  python -c "from schema import apply_indexes; apply_indexes()" || true

  if [ -f "$ERP_SEED_FILE" ]; then
    echo "[entrypoint] importing ERP demo subset from $ERP_SEED_FILE..."
    python import_new_erp_subset.py --input "$ERP_SEED_FILE"
  else
    echo "[entrypoint] no seed data found — starting empty"
  fi
else
  echo "[entrypoint] ERP graph already has $EXISTING machines — skipping ERP seed"
fi

if [ -n "${EXXAS_INITIAL_WATERMARK:-}" ]; then
  echo "[entrypoint] ensuring Exxas watermark (initial: $EXXAS_INITIAL_WATERMARK)..."
  python -c "
import os
from db import db
from exxas_daily_sync import initialize_watermark, get_state_watermark
db.connect()
if not get_state_watermark():
    initialize_watermark(os.environ['EXXAS_INITIAL_WATERMARK'])
    print('[entrypoint] Exxas watermark set to', os.environ['EXXAS_INITIAL_WATERMARK'])
else:
    print('[entrypoint] Exxas watermark already set — skipping')
" || true
fi

echo "[entrypoint] applying Proto schema + indexes..."
python -c "from proto.schema import apply_indexes; apply_indexes()" || true

echo "[entrypoint] starting uvicorn"
exec python -m uvicorn proto_server:app --host 0.0.0.0 --port 8000
