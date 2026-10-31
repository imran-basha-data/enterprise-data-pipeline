-- Operational metadata and the immutable raw landing layer.

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS raw;

-- One row per pipeline execution.
CREATE TABLE ops.pipeline_runs (
    run_id              uuid PRIMARY KEY,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    status              text NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'succeeded', 'failed', 'abandoned')),
    files_discovered    integer NOT NULL DEFAULT 0,
    files_loaded        integer NOT NULL DEFAULT 0,
    files_skipped       integer NOT NULL DEFAULT 0,
    files_rejected      integer NOT NULL DEFAULT 0,
    records_loaded      integer NOT NULL DEFAULT 0,
    records_quarantined integer NOT NULL DEFAULT 0,
    error_message       text
);

-- File manifest. The unique (entity, checksum) constraint is what makes
-- re-running the pipeline over the same landing zone a no-op.
CREATE TABLE ops.ingested_files (
    file_id             bigserial PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES ops.pipeline_runs (run_id),
    entity              text NOT NULL,
    file_name           text NOT NULL,
    checksum_sha256     char(64) NOT NULL,
    file_size_bytes     bigint NOT NULL,
    status              text NOT NULL CHECK (status IN ('loaded', 'rejected')),
    rejection_reason    text,
    schema_drift        jsonb,
    rows_read           integer NOT NULL DEFAULT 0,
    rows_inserted       integer NOT NULL DEFAULT 0,
    rows_updated        integer NOT NULL DEFAULT 0,
    rows_unchanged      integer NOT NULL DEFAULT 0,
    rows_stale          integer NOT NULL DEFAULT 0,
    rows_duplicate      integer NOT NULL DEFAULT 0,
    rows_quarantined    integer NOT NULL DEFAULT 0,
    processed_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_ingested_files_entity_checksum UNIQUE (entity, checksum_sha256)
);

-- Records that failed validation, with every reason recorded.
CREATE TABLE ops.quarantine (
    quarantine_id       bigserial PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES ops.pipeline_runs (run_id),
    file_id             bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    entity              text NOT NULL,
    row_number          integer NOT NULL,
    record_key          text,
    reason_codes        text[] NOT NULL,
    issues              jsonb NOT NULL,
    payload             jsonb NOT NULL,
    quarantined_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_quarantine_file_row UNIQUE (file_id, row_number)
);
CREATE INDEX ix_quarantine_entity_reason ON ops.quarantine USING gin (reason_codes);

-- Results of file-level and dataset-level data-quality checks.
CREATE TABLE ops.dq_results (
    result_id           bigserial PRIMARY KEY,
    run_id              uuid NOT NULL REFERENCES ops.pipeline_runs (run_id),
    file_id             bigint REFERENCES ops.ingested_files (file_id),
    check_name          text NOT NULL,
    entity              text,
    severity            text NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    passed              boolean NOT NULL,
    observed_value      numeric,
    threshold           numeric,
    details             jsonb,
    checked_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_dq_results_run ON ops.dq_results (run_id);

-- Raw layer: every source row exactly as received, for audit and replay.
CREATE TABLE raw.landed_records (
    file_id             bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    row_number          integer NOT NULL,
    payload             jsonb NOT NULL,
    landed_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (file_id, row_number)
);
