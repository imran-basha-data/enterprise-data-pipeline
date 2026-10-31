# Troubleshooting

## `configuration error: EDP_DATABASE_URL is not set` (exit code 2)

Load your `.env` into the shell (`set -a && source .env && set +a`) or export the variable directly. Every other setting has a default.

## `run_locked` / exit code 3

Another run holds the advisory lock. Find it:

```sql
SELECT pid, application_name, state, query_start
FROM pg_stat_activity
WHERE pid IN (SELECT pid FROM pg_locks WHERE locktype = 'advisory');
```

If the process has gone, PostgreSQL releases the lock when its session ends. You never need to clear it by hand.

## A file was not loaded

```sql
SELECT file_name, status, rejection_reason, schema_drift
FROM ops.ingested_files ORDER BY processed_at DESC LIMIT 20;
```

| `rejection_reason` starts with | Meaning | Fix |
|---|---|---|
| `missing_required_columns` | header lacks a required column | correct the export; the corrected file has a new checksum and will load |
| `unexpected_columns` | extra column under `EDP_SCHEMA_DRIFT_POLICY=reject` | add the column to the contract and a migration, or switch the policy to `warn` |
| `unreadable_file` | invalid JSON or no CSV header | re-export the file |
| `reject_rate_exceeded` | too many invalid rows | inspect `ops.quarantine` for that `file_id`; usually an upstream export bug |

If the file doesn't appear in the manifest at all, check that it follows `<entity>__<batch>.<csv|json|jsonl>`. Unknown names are logged as `file_ignored`.

## I fixed a file but it still says `skipped`

Files are recognised by **content**, not by name. If the corrected file is byte-for-byte identical to the rejected one, nothing changed. Make sure the fix was saved.

## Why was a record quarantined?

```sql
SELECT row_number, record_key, reason_codes, issues, payload
FROM ops.quarantine WHERE file_id = <file_id> ORDER BY row_number;
```

`issues` lists every problem with its field and a readable message. `payload` is the row exactly as received.

## Run failed with `blocking data-quality checks failed`

```sql
SELECT check_name, observed_value, details
FROM ops.dq_results
WHERE run_id = (SELECT run_id FROM ops.pipeline_runs ORDER BY started_at DESC LIMIT 1)
  AND NOT passed AND severity = 'error';
```

The mart refresh was rolled back, so consumers still see the last good state. Reconciliation failures mean staging and marts disagree, which points to a model or watermark bug. To rebuild the marts from staging once the cause is fixed:

```bash
edp run --full-refresh
```

## `MigrationError: migration ... was modified after being applied`

An applied migration file was edited. Restore the original and put the change in a new numbered migration.

## Negative stock in `v_inventory_position`

The `negative_stock_positions` warning usually means inbound movements (receipts, transfers in, production output) were quarantined for that product and warehouse. Check `ops.quarantine` for `entity = 'inventory_movements'`.

## Docker: `permission denied` writing to `data/landing` or `reports`

On Linux, bind-mounted folders belong to your host user. Set `HOST_UID=$(id -u)` and `HOST_GID=$(id -g)` in `.env` so the container runs as you.

## Docker build fails with `CERTIFICATE_VERIFY_FAILED`

You are behind a TLS-intercepting proxy. Pass its CA bundle as a build secret. The secret is never stored in an image layer.

```bash
docker build --secret id=ca_bundle,src=/path/to/corporate-ca.pem -t enterprise-data-pipeline:local .
```

## Tests are skipped locally

Set `EDP_TEST_DATABASE_URL` to a disposable database. Set `EDP_REQUIRE_DB=1` to make a missing database an error instead.
