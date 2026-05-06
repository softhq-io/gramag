# Gramag Knowledge Graph

Knowledge assistant for **Gramag Grafische Maschinen AG** — a Swiss company servicing industrial printing & finishing machines (MBO, Heidelberg, Müller Martini, etc.).

Combines a 5-layer knowledge graph (ERP data, service history, PDF manuals, co-occurrence mining) with Gemini embeddings for hybrid retrieval. Technicians ask questions in natural language and get answers grounded in machine-specific service history, spare parts, and manual references.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Vite + TS   │────▶│  FastAPI      │────▶│  FalkorDB       │
│  (web/)      │     │  server.py    │     │  (graph DB)     │
└─────────────┘     │  proto_server │     └─────────────────┘
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │  Gemini API   │
                     │  embeddings   │
                     │  + chat       │
                     └──────────────┘
```

**Knowledge Graph Layers:**
- **L1 Inventory** — Customers, Machines, MachineTypes, MachineBrands, Parts, Suppliers
- **L2 ERP** — Full ERP import: Dokumente, Kontakte, Emails, Aufgaben, Historie, Lagerbestand, etc.
- **L3 Service** — ServiceJobs, ServiceComments, DokLeistungen (with parts used)
- **L4 Manuals** — PDF sections with embeddings, ErrorCodes, TroubleshootingEntries
- **L5 Playbook** — OFTEN_USED_WITH edges from co-occurrence mining

**Proto KB** — Multimodal knowledge base (14 machines from Beorda customer dump) with vision-extracted page sections. Deployed at gramag-proto.fly.dev.

## Quick Start

```bash
# 1. Start FalkorDB
docker compose up -d

# 2. Restore graph from seed (or rebuild from raw data)
docker cp seed/dump.rdb gramag-falkordb-1:/var/lib/falkordb/data/dump.rdb
docker compose restart falkordb

# 3. Set up Python
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env — add your GEMINI_API_KEY

# 5. Run
uvicorn server:app --port 8000
```

The precomputed embeddings are in `index/` (tracked via Git LFS). The vector search works out of the box after restoring the graph.

## API

| Endpoint | Description |
|---|---|
| `POST /api/ask` | v1 — vector-only Q&A |
| `POST /api/ask/v2` | v2 — hybrid (graph + vector) Q&A |
| `GET /api/machine/{erp_id}` | Machine details |
| `GET /api/part/{nummer}` | Part details |
| `GET /api/graph/stats` | Graph statistics |
| `POST /api/proto/ask` | Proto KB Q&A (multimodal) |
| `GET /api/proto/machines` | Proto machine list |

## Seeding from raw data

If you have the original CSV/PDF data in `data/Gramag Daten/`:

```bash
python seed_erp.py         # L1: machines, customers, brands, types
python seed_all_csv.py     # L2: full ERP import (all 115 CSV tables)
python seed_pdfs.py        # L3: PDF manual sections + embeddings
python mining.py           # L4: co-occurrence mining
```

## Frontend

```bash
cd web
npm install
npm run dev
```

Set `VITE_PROTO_ONLY=1` to build proto-only frontend for Fly deployment.

## Deployment (Fly.io)

See [DEPLOY.md](DEPLOY.md) for the proto KB deployment guide.

## Stack

- **Python 3.14** / FastAPI / uvicorn
- **FalkorDB** (Redis-based graph database)
- **Gemini** (embeddings: gemini-embedding-001, chat: gemini-3-pro-preview)
- **Vite + TypeScript + React** (frontend)
