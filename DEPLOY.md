# Deploying Gramag proto KB to Fly.io

One-time setup that brings the current local FalkorDB state + source files
+ page cache to a Fly volume. After that, `fly deploy` ships just the code.

## 0. Prerequisites

```bash
fly auth login        # if not already
which fly             # should print path
```

Check that local FalkorDB is running and has data:

```bash
docker ps | grep gramag-falkordb
source .venv/bin/activate && python -c "
from proto.db_proto import proto_db
print(proto_db.stats())
"
```

You should see `Machine`, `Document`, `ManualSection`, `ConfigFile`,
`ImageAsset` counts > 0.

## 1. Migrate paths to portable form (once, already done if you ran it)

```bash
source .venv/bin/activate
python -m proto.migrate_paths
```

Rewrites `Document.path`, `ImageAsset.path`, `ManualSection.png_path` in the
graph to be relative to `PROTO_ROOT` / `PROTO_CACHE_DIR`. Idempotent.

## 2. Create the bundle (~600MB)

```bash
chmod +x deploy/make_bundle.sh
deploy/make_bundle.sh
# → /tmp/gramag-data.tar.gz
```

Contains:
- `falkor/dump.rdb` — FalkorDB state (gramag_proto graph)
- `source/` — drive-download tree (original PDFs, images, TXT configs)
- `cache/pages/` — rendered PDF page PNGs
- `manifest.json` — for re-ingest later

## 3. Create the Fly app (no deploy yet)

```bash
# Pick app name — default is in fly.toml as `gramag-proto`
fly launch --copy-config --no-deploy --name gramag-proto --region fra
```

## 4. Create the data volume

```bash
fly volumes create gramag_data --size 3 --region fra
```

## 5. Upload the bundle to the volume

Spin up a one-off machine with the volume mounted, sftp the bundle in, then
extract:

```bash
# Start a shell machine with volume attached
fly ssh console --select -C "sh"  # pick any running or spin up a temp one

# OR use sftp directly — needs a running machine. First time, deploy with a
# minimal placeholder to boot the machine:
fly deploy --ha=false

# Then upload (from another terminal):
fly sftp shell
  > cd /data
  > put /tmp/gramag-data.tar.gz
  > exit

# SSH in and extract
fly ssh console -C "sh -c 'cd /data && tar xzf gramag-data.tar.gz && rm gramag-data.tar.gz && ls -la'"
```

You should see:
```
/data/falkor/dump.rdb
/data/source/<machine folders>
/data/cache/pages/<sha>/p0001.png ...
```

## 6. Set Azure OpenAI and auth secrets

```bash
fly secrets set AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
fly secrets set AZURE_OPENAI_API_KEY=your_key_here
fly secrets set AZURE_OPENAI_CHAT_DEPLOYMENT=gramag-chat
fly secrets set AZURE_OPENAI_VISION_DEPLOYMENT=gramag-vision
fly secrets set AZURE_OPENAI_EMBED_DEPLOYMENT=gramag-embed
fly secrets set JWT_SECRET=$(openssl rand -hex 32)
```

## 7. Deploy the real app

```bash
fly deploy
```

FalkorDB boots reading `/data/falkor/dump.rdb`, uvicorn serves `/`, frontend
is at `/einsatzplaner/proto`. First request wakes the machine from
`auto_stop_machines = "suspend"`.

## 8. Bootstrap the first superadmin (one time)

```bash
fly ssh console -C "cd /app && python manage_users.py bootstrap --email admin@example.com --name Admin"
```

The command prints a temporary password once. Sign in and replace it immediately.

Before any migration write, run the environment's data-backup workflow and wait
for it to finish successfully. For Azure staging this is the manual
`Backup Staging Data` GitHub Actions workflow; it forces FalkorDB persistence
and snapshots both Azure File shares.

After verifying the superadmin, preview the legacy-user migration and Proto links:

```bash
fly ssh console -C "cd /app && python proto_erp_link.py --dry-run"
fly ssh console -C "cd /app && python manage_users.py migrate-legacy --dry-run"
```

The legacy migration updates existing user nodes in place. It preserves their
password hashes, keeps their usernames as login identifiers, activates them with
the `all_clients` role, and never deletes business or chat data. If no real email
is supplied, a unique `@legacy.invalid` alias is used. Real emails can be mapped
explicitly during both preview and execution:

```bash
fly ssh console -C "cd /app && python manage_users.py migrate-legacy --dry-run --email admin=admin@example.com --email techniker=service@example.com"
```

After reviewing both dry-run reports and confirming the backup snapshot exists:

```bash
fly ssh console -C "cd /app && python proto_erp_link.py"
fly ssh console -C "cd /app && python manage_users.py migrate-legacy --email admin=admin@example.com --email techniker=service@example.com"
```

Review the dry-run report before writing links. Regular users fail closed for
Proto machines that do not have a stable `erp_customer_id`.

For shell-only recovery of an existing superadmin:

```bash
fly ssh console -C "cd /app && python manage_users.py recover --email admin@example.com"
```

## 9. Verify

```bash
curl https://gramag-proto.fly.dev/health
# { "status": "ok" }
```

Open `https://gramag-proto.fly.dev/einsatzplaner/proto`, log in, test a query.

## Future: scaling to all 14 machines

The multimodal ingest can run locally against Azure OpenAI quota and the
resulting dump.rdb + new cache pages re-bundled and re-uploaded:

```bash
# locally
python -m proto.ingest --all --workers 8
deploy/make_bundle.sh

# push new bundle
fly sftp shell
  > put /tmp/gramag-data.tar.gz /data/gramag-data.tar.gz
fly ssh console -C "sh -c 'cd /data && tar xzf gramag-data.tar.gz && rm gramag-data.tar.gz'"
fly machine restart <machine-id>   # picks up new dump.rdb
```

Or run ingest *inside* Fly (slower initial spin-up but doesn't load your
local machine):

```bash
fly ssh console -C "cd /app && python -m proto.ingest --all --workers 8"
```

## Troubleshooting

- **Machine keeps restarting:** check `fly logs`. Often FalkorDB module path
  mismatch — `/entrypoint.sh` auto-scans but `find` can be slow. Hardcode
  once you see the real path.
- **Empty graph after deploy:** verify `/data/falkor/dump.rdb` exists on the
  volume (`fly ssh console -C "ls -la /data/falkor"`) and that it loaded
  (`fly logs` should show `DB loaded from disk`).
- **PDF viewer shows 404:** paths in graph are relative; make sure
  `/data/source/<folder>` matches whatever the graph stores. Run
  `fly ssh console -C "cd /app && python -c 'from proto import resolve_source; print(resolve_source(\"SMB\"))'"`.
