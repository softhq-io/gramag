# Proto SharePoint Ingest Runbook

This document describes the current durable Proto KB ingest process for
SharePoint customer folders. The current production-like proof run is
`Birkhäuser + GBC AG` on staging; the same process is intended for the rest of
the SharePoint customer tree.

## Goal

The ingest must be append-only and resumable:

- Do not drop the `gramag_proto` graph during customer ingestion.
- Do not share checkpoint files between concurrently running shards.
- Do not mark files complete until their durable output or graph write has
  succeeded.
- Take app-aware FalkorDB snapshots before and after import phases.
- Treat staged JSONL as the durable handoff between expensive extraction and
  graph writes.

## Current Architecture

The process has two separated stages:

1. **Extraction**
   - Mirrors the selected SharePoint source into an isolated local source path.
   - Builds a Proto manifest for the selected shard.
   - Runs vision/text extraction.
   - Writes durable JSONL records under `/data/proto-stage/...`.
   - Does not write extracted content to FalkorDB.

2. **Import**
   - Reads staged JSONL records.
   - Writes them to the shared `gramag_proto` FalkorDB graph as the only graph
     writer for that phase.
   - Uses an import checkpoint so reruns continue from the last successful
     record.

This split is deliberate. PDF/image extraction is slow and expensive; graph
writes are sensitive to FalkorDB availability. Keeping these separate prevents
partial graph write failures from wasting completed extraction work.

## Important Paths

Staging uses isolated paths per phase/shard:

- Source mirror: `/data/source-...`
- Extraction cache/checkpoint: `/data/cache-...`
- Manifest: `/data/manifest-....json`
- Staged PDF/text JSONL: `/data/proto-stage/birkhaeuser-pdf`
- Staged image JSONL: `/data/proto-stage/birkhaeuser-image`
- Import checkpoints: `import_checkpoint.json` inside the stage directory
- Durable job logs: `/data/ingest-logs/<job>/<execution>.log`

Every shard must own its own source, cache, manifest, and SharePoint delta
state. Shards may write to the same graph only during the controlled import
phase, or when their write sets are known to be safe.

## Staging Workflows

The manual runner is:

- `.github/workflows/run-staging-proto-ingest.yml`

Supported phases:

- `pdf-text`: start the four Birkhäuser PDF/text extraction shards.
- `pdf-import`: import staged PDF/text JSONL into FalkorDB.
- `image`: start the four Birkhäuser image extraction shards.
- `image-import`: import staged image JSONL into FalkorDB.
- `custom`: start a named Container App Job.

The automatic resume helper is:

- `.github/workflows/resume-staging-proto-extract.yml`

It currently monitors and resumes Birkhäuser PDF extraction shards. It only
restarts failed or missing idle shards after:

- confirming no shard is still active,
- running FalkorDB `BGSAVE`,
- verifying persistence settings,
- snapshotting `falkordb-data` and `app-data`.

Backups are handled by:

- `.github/workflows/backup-staging-data.yml`
- `.github/workflows/backup-prod-data.yml`

These workflows run app-aware snapshots every six hours. Before snapshotting
they verify:

- `appendonly=yes`
- `appendfsync=everysec`
- `dir=/var/lib/falkordb/data`
- no RDB save or AOF rewrite is in progress
- last RDB/AOF statuses are OK

Snapshot retention currently keeps 14 days while always preserving at least
eight snapshots per share.

## Safe Phase Sequence

Use this sequence for a full customer ingest:

1. Mirror SharePoint and prepare shard definitions.
2. Run PDF/text extraction shards.
3. Confirm all extraction shards succeeded and staged JSONL exists.
4. Run `BGSAVE`, verify persistence, snapshot `falkordb-data` and `app-data`.
5. Run PDF/text import.
6. Verify graph counts and machine coverage.
7. Run `BGSAVE`, verify persistence, snapshot both shares.
8. Run image extraction shards.
9. Confirm all image extraction shards succeeded and staged JSONL exists.
10. Run `BGSAVE`, verify persistence, snapshot both shares.
11. Run image import.
12. Verify graph counts, machine image counts, and job success.
13. Run final `BGSAVE`, verify persistence, snapshot both shares.

Do not start an import phase if extraction failed. Do not start image import if
PDF/text import did not complete cleanly.

## Completion Criteria

A customer is considered synced only after all relevant stages are imported into
FalkorDB, not merely extracted.

For Birkhäuser on staging, the completed reference state is:

- `Customer`: 1
- `Machine`: 13
- `Document`: 4980
- `DocumentCategory`: 59
- `ManualSection`: 28609
- `ConfigFile`: 1250
- `ImageAsset`: 3218
- `HAS_IMAGE`: 3218

The image import execution was:

- Job: `staging-sp-proto-birk-import-img`
- Execution: `staging-sp-proto-birk-import-img-ey5eqs7`
- Status: `Succeeded`
- Runtime: `2026-06-23T05:12:10Z` to `2026-06-23T05:19:14Z`

Post-import snapshots were taken after a clean `BGSAVE`:

- `falkordb-data`: `2026-06-23T05:20:39Z`
- `app-data`: `2026-06-23T05:20:40Z`

## Monitoring Checks

Public health:

```bash
curl --max-time 30 -fsS \
  https://staging-gramag-app.politemushroom-9223672b.westeurope.azurecontainerapps.io/health
```

Machine coverage:

```bash
curl --max-time 30 -fsS \
  https://staging-gramag-app.politemushroom-9223672b.westeurope.azurecontainerapps.io/api/proto/machines
```

Container App Job status:

```bash
az containerapp job execution list \
  --resource-group gramag-staging-rg \
  --name staging-sp-proto-birk-import-img \
  --query "sort_by(@,&properties.startTime)[-3:].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}" \
  --output table
```

Container App Job logs:

```bash
az containerapp job logs show \
  --resource-group gramag-staging-rg \
  --name staging-sp-proto-birk-import-img \
  --execution <execution-name> \
  --container sharepoint-proto-ingest \
  --tail 160
```

Staged JSONL record counts:

```bash
az containerapp exec \
  --resource-group gramag-staging-rg \
  --name staging-gramag-app \
  --container gramag-app \
  --command "find /data/proto-stage/birkhaeuser-image -maxdepth 1 -type f -name '*.jsonl' -exec wc -l {} +"
```

## Manual Snapshot Commands

Run `BGSAVE`:

```bash
az containerapp exec \
  --resource-group gramag-staging-rg \
  --name staging-gramag-falkordb \
  --container falkordb \
  --command "redis-cli BGSAVE"
```

Check persistence:

```bash
az containerapp exec \
  --resource-group gramag-staging-rg \
  --name staging-gramag-falkordb \
  --container falkordb \
  --command "redis-cli INFO persistence"
```

Required clean state:

- `loading:0`
- `rdb_bgsave_in_progress:0`
- `rdb_last_bgsave_status:ok`
- `aof_enabled:1`
- `aof_rewrite_in_progress:0`
- `aof_last_bgrewrite_status:ok`
- `aof_last_write_status:ok`

Take snapshots:

```bash
key="$(az storage account keys list \
  --resource-group gramag-staging-rg \
  --account-name gramagstagingjcf0gh \
  --query '[0].value' \
  --output tsv)"

for share in falkordb-data app-data; do
  az storage share snapshot \
    --account-name gramagstagingjcf0gh \
    --account-key "$key" \
    --name "$share" \
    --output table
done
```

## Scaling Beyond Birkhäuser

The next step is to generalize the Birkhäuser shard pattern across all
customers:

1. Generate shard definitions from SharePoint customer and machine metadata.
2. Balance by estimated PDF pages first, then image/file count.
3. Keep each shard disjoint by customer/machine folder.
4. Start with four shards.
5. Increase only after Azure OpenAI throttling and FalkorDB latency remain
   stable.
6. Keep the same staged extraction and single-writer import pattern.

The target is not a Birkhäuser-specific pipeline. Birkhäuser is the proof run;
all clients should use the same staged extraction, import checkpoints, and
snapshot protocol.

## Preparing A 16-Session Batch

Use `proto.shards` to turn a manifest into Terraform shard definitions. The
recommended comfortable staging setup after the OpenAI capacity increase is 16
parallel extraction sessions:

```bash
python -m proto.shards \
  --manifest-path /path/to/manifest.json \
  --shards 16 \
  --name-prefix clients-a \
  --stage-prefix clients-a \
  --terraform \
  --output /tmp/clients-a-shards.tfvars
```

If the staging tfvars already contains other shard jobs and the new batch should
be merged into that map, render only the map body:

```bash
python -m proto.shards \
  --manifest-path /path/to/manifest.json \
  --shards 16 \
  --name-prefix clients-a \
  --stage-prefix clients-a \
  --terraform \
  --no-assignment \
  --output /tmp/clients-a-shards-body.tfvars
```

The generated map contains:

- 16 PDF/text extraction jobs named like `clients-a01-pdf`.
- 16 image extraction jobs named like `clients-a01-img`.
- one PDF/text import job named `clients-a-import-pdf`.
- one image import job named `clients-a-import-img`.

Each extraction job is one session:

- `ingest_workers = 1`
- `ingest_img_workers = 1`
- `ingest_machine_workers = 1`
- isolated `PROTO_ROOT`
- isolated `PROTO_CACHE_DIR`
- isolated `PROTO_MANIFEST_PATH`
- isolated `SHAREPOINT_DELTA_STATE`

Review the generated include paths before applying. The shard generator assigns
each machine folder to exactly one PDF/text shard and exactly one image shard.
It does not start jobs and does not modify Terraform by itself.

To activate the batch, paste or merge the generated
`sharepoint_proto_ingest_shards` map into the staging tfvars, apply staging
infra, then start only the desired phase through
`.github/workflows/run-staging-proto-ingest.yml` or explicit Container App Job
starts. Keep imports single-writer.

For a generated batch with `--name-prefix clients-a`, use the manual workflow
inputs:

- `phase=batch-pdf-text`, `batch_prefix=clients-a`, `batch_count=16`
- `phase=batch-pdf-import`, `batch_prefix=clients-a`
- `phase=batch-image`, `batch_prefix=clients-a`, `batch_count=16`
- `phase=batch-image-import`, `batch_prefix=clients-a`

The workflow expands these into Container App Job names like
`staging-sp-proto-clients-a01-pdf` through
`staging-sp-proto-clients-a16-pdf`. It does not snapshot automatically; keep the
safe phase sequence above.

## Failure Rules

- If extraction fails, rerun the same shard. Staged checkpoints should skip
  already completed files.
- If import fails, rerun the same import job. The import checkpoint should skip
  records that were already written successfully.
- If FalkorDB is loading or public health times out, stop starting new ingest
  work and wait for recovery.
- If graph counts drop, do not start new imports. Restore investigation has
  priority over progress.
- Never overwrite a live file share from a backup until the restored share has
  been mounted and inspected separately.
