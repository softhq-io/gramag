# Gramag Knowledge Graph

Knowledge assistant for **Gramag Grafische Maschinen AG** — a Swiss company servicing industrial printing & finishing machines (MBO, Heidelberg, Müller Martini, etc.).

Combines a 5-layer knowledge graph (ERP data, service history, PDF manuals, co-occurrence mining) with Azure OpenAI embeddings for hybrid retrieval. Technicians ask questions in natural language and get answers grounded in machine-specific service history, spare parts, and manual references.

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Vite + TS   │────▶│  FastAPI      │────▶│  FalkorDB       │
│  (web/)      │     │  server.py    │     │  (graph DB)     │
└─────────────┘     │  proto_server │     └─────────────────┘
                     └──────┬───────┘
                            │
                     ┌──────▼───────┐
                     │ Azure OpenAI  │
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
# Edit .env — add AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY

# 5. Run
python -m uvicorn proto_server:app --port 8000
```

The precomputed embeddings are in `index/` (tracked via Git LFS). After changing embedding providers or deployments, rebuild them with `python rebuild_embeddings.py` and refresh FalkorDB vectors with `python refresh_graph_embeddings.py`.

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

## Proto SharePoint Sync

Proto KB can mirror a SharePoint document library or folder into `PROTO_ROOT`,
rebuild `PROTO_MANIFEST_PATH`, then let the normal Proto ingest process handle
new and changed files.

Required app-only Graph credentials:

```bash
export SHAREPOINT_TENANT_ID="..."
export SHAREPOINT_CLIENT_ID="..."
export SHAREPOINT_CLIENT_SECRET="..."
```

Also provide either a site ID, or the hostname plus site path:

```bash
export SHAREPOINT_SITE_HOSTNAME="contoso.sharepoint.com"
export SHAREPOINT_SITE_PATH="/sites/Engineering"
export SHAREPOINT_DRIVE_NAME="Documents"        # optional if the default drive is correct
export SHAREPOINT_ROOT_PATH="Machines"          # optional folder inside the library
export PROTO_ROOT_MODE="machines"               # or "customers" when root contains customer folders
```

Or provide the SharePoint browser URL directly:

```bash
export SHAREPOINT_WEB_URL="https://contoso.sharepoint.com/sites/Engineering/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FEngineering%2FShared%20Documents%2FMachines"
```

Mirror updates and rebuild the manifest:

```bash
python sharepoint_proto_sync.py
```

For the Services customer folder layout, use the parent customer folder and
customer mode:

```bash
export SHAREPOINT_ROOT_PATH="Kundendienst/Kunden"
export PROTO_ROOT_MODE="customers"
python sharepoint_proto_sync.py --run-ingest --ingest-all
```

For a staging smoke test with one customer only, point at that customer folder
and attach the customer name:

```bash
export SHAREPOINT_ROOT_PATH="Kundendienst/Kunden/Beorda Direktwerbung AG"
export PROTO_ROOT_MODE="machines"
export PROTO_CUSTOMER_NAME="Beorda Direktwerbung AG"
python sharepoint_proto_sync.py --run-ingest --ingest-all
```

Azure staging is configured the same way via Terraform. Keep SharePoint
credentials out of `terraform.tfvars` and provide them as sensitive variables:

```bash
export TF_VAR_sharepoint_tenant_id="..."
export TF_VAR_sharepoint_client_id="..."
export TF_VAR_sharepoint_client_secret="..."
```

The staging Container App Job runs daily from Graph delta state stored in
`/data/cache/sharepoint_delta_state.json`; the first run populates the selected
customer folder, later runs process only deltas.

Mirror and immediately ingest all changed Proto sources:

```bash
python sharepoint_proto_sync.py --run-ingest --ingest-all
```

The sync stores the Microsoft Graph delta token in
`$PROTO_CACHE_DIR/sharepoint_delta_state.json` by default. Use `--full` to
ignore that token and rescan the selected library or folder.

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
- **Azure OpenAI** (default deployments: gpt-5-mini, gpt-5-chat, text-embedding-3-large)
- **Vite + TypeScript + React** (frontend)
