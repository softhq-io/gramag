# Managed Knowledge Ingestion

The normal knowledge workflow is CRM-driven and document-scoped:

1. A superadmin activates an existing CRM client in **Knowledge**.
2. A superadmin or `all_clients` technician selects CRM machines.
3. A manager uploads PDF, image, or text documents to one selected machine.
4. The upload is stored under `/data/managed-documents` and queued in the
   `gramag_proto` graph.
5. `proto.managed_ingest_worker` leases the job, extracts and embeds the file,
   and changes the document to `ready` only after graph verification.

The UI polls the document status while it is `queued`, `processing`, or
`deleting`. Transient failures retry after 30 seconds, two minutes, and ten
minutes. A terminal failure can be retried from the UI.

## Legacy adoption

Take FalkorDB and app-data snapshots, then preview the migration:

```bash
python migrate_managed_knowledge.py
```

Review `clients_to_activate` and every quarantined machine. Apply only after
the report is understood:

```bash
python migrate_managed_knowledge.py --apply
```

Map quarantined machines with an optional JSON file keyed by their preserved
Proto slug. A value may be a CRM client ID, or an object with `client_id` and
an optional `erp_machine_id`; CRM ownership is validated before anything is
written:

```json
{
  "legacy-machine-slug": {
    "client_id": "crm-client-id",
    "erp_machine_id": "crm-machine-id"
  },
  "grouped-legacy-slug": "crm-client-id"
}
```

```bash
python migrate_managed_knowledge.py --mapping-file legacy-mappings.json
python migrate_managed_knowledge.py --apply --mapping-file legacy-mappings.json
```

The migration preserves machine slugs, document IDs, derived content, and chat
references. Machines without an ERP customer link are quarantined and must be
resolved before they become visible to scoped users.

## SharePoint recovery path

SharePoint mirroring and the manual shard jobs remain available as legacy
recovery/import tooling. `sharepoint_proto_schedule_enabled` must remain
`false` during normal operation; otherwise a deleted legacy file could be
mirrored back into app-data.

## Operational checks

- `/health` reports total and actionable queue depth, active processing, terminal
  failure count, worker heartbeat, and whether actionable work is stalled.
- The managed worker must have exactly one replica and share the app-data mount.
- Investigate a heartbeat older than two minutes, a terminal failure, or
  actionable work older than ten minutes when no processing job has made
  progress in that interval. A queue waiting behind a progressing document or
  a scheduled retry backoff is not stalled.
- Permanent deletion removes the original, cached pages, and derived graph
  nodes. Recovery requires an infrastructure backup.
