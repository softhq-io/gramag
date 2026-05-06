# syntax=docker/dockerfile:1.6
# Gramag proto KB — single-container deploy for Fly.io.

# ── 1. Frontend build ────────────────────────────────────────────
FROM node:20-alpine AS web-build
WORKDIR /web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build


# ── 2. Python dependencies ───────────────────────────────────────
FROM python:3.12-slim-bookworm AS py-build
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix /install -r requirements.txt


# ── 3. Grab the FalkorDB module (redis loadable .so) ─────────────
FROM falkordb/falkordb:latest AS falkor-src
# Locate the module at image build time and stage it at a known path.
RUN set -eux; \
    mod=$(find / -name 'falkordb.so' 2>/dev/null | head -n1); \
    echo "Found at: $mod"; \
    mkdir -p /stage; cp "$mod" /stage/falkordb.so


# ── 4. Runtime: Python slim + redis + FalkorDB module + app ──────
FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg lsb-release \
    && curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb bookworm main" \
        > /etc/apt/sources.list.d/redis.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         redis tini libgomp1 libssl3 libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# FalkorDB module
COPY --from=falkor-src /stage/falkordb.so /usr/lib/redis/modules/falkordb.so

# Python packages
COPY --from=py-build /install /usr/local

WORKDIR /app

# App code — proto KB + ERP
COPY proto/ ./proto/
COPY proto_server.py config.py db.py db_helpers.py embeddings.py ./
COPY auth.py auth_router.py seed_users.py ./
COPY erp_router.py retriever.py schema.py import_new_erp_subset.py ./
COPY mission_router.py mission.py fleet_router.py fleet.py ./

# ERP demo seed data (6 machines)
COPY demo_subset_full_history_6.json ./data/demo_subset.json

# Built frontend
COPY --from=web-build /web/dist ./web/dist

# Supervisor script
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Paths point to the Fly volume mount
ENV PROTO_ROOT=/data/source \
    PROTO_CACHE_DIR=/data/cache \
    PROTO_MANIFEST_PATH=/data/manifest.json \
    FALKORDB_HOST=127.0.0.1 \
    FALKORDB_PORT=6379 \
    FALKORDB_GRAPH=gramag \
    FALKORDB_DATA_DIR=/data/falkor \
    FALKORDB_MODULE=/usr/lib/redis/modules/falkordb.so \
    ERP_SEED_FILE=/app/data/demo_subset.json \
    PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["tini", "-g", "--"]
CMD ["/entrypoint.sh"]
