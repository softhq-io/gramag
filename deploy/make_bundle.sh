#!/bin/bash
# Creates /tmp/gramag-data.tar.gz with everything the Fly volume needs:
#   /data/falkor/dump.rdb    — only the gramag_proto graph (exported fresh)
#   /data/source/...         — original source files (drive-download tree)
#   /data/cache/pages/...    — rendered PDF page PNGs
#
# The bundle is ~600MB. Upload it once to /data/ on the Fly volume via
# `fly sftp`, then extract in-place (see DEPLOY.md).

set -euo pipefail

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
SOURCE_DIR=${PROTO_ROOT:-/Users/piotrzwolinski/Downloads/drive-download-20260414T171809Z-3-001}
CACHE_DIR=${PROTO_CACHE_DIR:-$PROJECT_DIR/proto/cache}
OUT=/tmp/gramag-data.tar.gz
STAGE=/tmp/gramag-bundle-stage

echo "[bundle] staging at $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE/falkor" "$STAGE/source" "$STAGE/cache/pages"

# 1. Export the gramag_proto graph only (not the main gramag graph)
echo "[bundle] dumping FalkorDB gramag_proto graph..."
if ! docker ps --format '{{.Names}}' | grep -q 'gramag-falkordb-1'; then
  echo "ERROR: FalkorDB container not running. Start it with 'docker compose up -d'." >&2
  exit 1
fi
# Force a fresh RDB save
docker exec gramag-falkordb-1 redis-cli SAVE > /dev/null
# Copy full dump — gramag + gramag_proto. The deployed app only reads gramag_proto
# (via FALKORDB_GRAPH env), so the extra data is harmless but wastes ~100MB.
# For a leaner dump, rebuild a proto-only instance; see DEPLOY.md note.
docker cp gramag-falkordb-1:/var/lib/falkordb/data/dump.rdb "$STAGE/falkor/dump.rdb"
echo "[bundle]   dump.rdb: $(du -h "$STAGE/falkor/dump.rdb" | cut -f1)"

# 2. Copy drive-download source tree
echo "[bundle] copying source files from $SOURCE_DIR..."
rsync -a --delete --exclude '.DS_Store' "$SOURCE_DIR/" "$STAGE/source/"
echo "[bundle]   source: $(du -sh "$STAGE/source" | cut -f1)"

# 3. Copy page cache
echo "[bundle] copying page cache..."
if [ -d "$CACHE_DIR/pages" ]; then
  rsync -a "$CACHE_DIR/pages/" "$STAGE/cache/pages/"
  echo "[bundle]   cache: $(du -sh "$STAGE/cache" | cut -f1)"
fi

# 4. Manifest (optional — convenient for re-ingest)
if [ -f "$PROJECT_DIR/proto/manifest.json" ]; then
  cp "$PROJECT_DIR/proto/manifest.json" "$STAGE/manifest.json"
fi

# 5. Tarball
echo "[bundle] creating $OUT..."
tar -czf "$OUT" -C "$STAGE" falkor source cache $([ -f "$STAGE/manifest.json" ] && echo manifest.json)
rm -rf "$STAGE"
echo "[bundle] done: $(du -h "$OUT" | cut -f1)  @ $OUT"
