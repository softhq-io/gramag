#!/bin/bash
# Entrypoint: start FalkorDB (backed by /data/falkor) then uvicorn.
set -euo pipefail

FALKOR_DIR=${FALKORDB_DATA_DIR:-/data/falkor}
mkdir -p "$FALKOR_DIR" /data/source /data/cache

# FalkorDB module path (staged by the Dockerfile)
MODULE_PATH=${FALKORDB_MODULE:-/usr/lib/redis/modules/falkordb.so}
if [ ! -f "$MODULE_PATH" ]; then
  MODULE_PATH=$(find / -name "falkordb.so" 2>/dev/null | head -n1 || true)
fi

echo "[entrypoint] starting FalkorDB (data=$FALKOR_DIR, module=$MODULE_PATH)"
redis-server \
  --port 6379 \
  --bind 127.0.0.1 \
  --loadmodule "$MODULE_PATH" \
  --dir "$FALKOR_DIR" \
  --dbfilename dump.rdb \
  --save 300 10 \
  --appendonly no \
  --stop-writes-on-bgsave-error no \
  --daemonize yes \
  --logfile /tmp/redis.log

# Wait for redis up
for i in $(seq 1 30); do
  if redis-cli -h 127.0.0.1 -p 6379 ping > /dev/null 2>&1; then
    echo "[entrypoint] FalkorDB ready"
    break
  fi
  sleep 0.5
done

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
