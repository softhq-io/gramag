# Seed Data

This directory contains a FalkorDB dump with the full knowledge graph.

## Contents

- `dump.rdb` — Redis/FalkorDB dump containing all graphs (`gramag`, `gramag_proto`)
- `../index/` — Precomputed Gemini embeddings (3072-dim, float32) + metadata

## Restoring the graph

```bash
# Stop FalkorDB if running
docker compose down

# Copy dump into FalkorDB data volume
docker compose up -d
docker cp seed/dump.rdb gramag-falkordb-1:/var/lib/falkordb/data/dump.rdb
docker compose restart falkordb
```

## Rebuilding from scratch (without seed)

If you have the raw CSV/PDF data in `data/Gramag Daten/`:

```bash
python seed_erp.py        # L1: machines, customers, brands, types
python seed_all_csv.py    # L2: full ERP import (all CSV tables)
python seed_pdfs.py       # L3: PDF manual sections + embeddings
python mining.py          # L4: co-occurrence mining (OFTEN_USED_WITH)
```

For the proto knowledge base (requires source PDFs):
```bash
python -m proto.ingest
```
