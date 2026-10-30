# Setup guide

## Prerequisites

| Option | Needs |
|---|---|
| Local Python | Python 3.11 or 3.12, PostgreSQL 16 (14+ should work but is not tested) |
| Docker only | Docker Engine 24+ with Compose v2 |

## Option A — Docker Compose (no local Python needed)

```bash
git clone https://github.com/imran-basha-data/enterprise-data-pipeline.git
cd enterprise-data-pipeline
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD. On Linux also set HOST_UID=$(id -u) and HOST_GID=$(id -g).
mkdir -p data/landing reports

docker compose --profile demo up --exit-code-from demo demo
```

This builds the image, starts PostgreSQL, generates synthetic data, runs the pipeline and writes `reports/kpi_report.md`.

To run the steps one at a time:

```bash
docker compose up -d postgres
docker compose --profile tools run --rm pipeline generate
docker compose --profile tools run --rm pipeline run
docker compose --profile tools run --rm pipeline report
psql "postgresql://edp:<password>@localhost:5432/edp" -c "SELECT * FROM mart.v_operational_kpis"
```

Tear down, including the database volume:

```bash
docker compose --profile demo --profile tools down -v
```

## Option B — local Python

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install --no-deps -e .
```

Create a database (example using `psql` as a superuser):

```sql
CREATE ROLE edp LOGIN PASSWORD 'choose-a-password';
CREATE DATABASE edp OWNER edp;
CREATE DATABASE edp_test OWNER edp;   -- disposable, used by the test suite
```

Configure and run:

```bash
cp .env.example .env          # set EDP_DATABASE_URL and EDP_TEST_DATABASE_URL
set -a && source .env && set +a

edp migrate                   # optional: `edp run` also applies pending migrations
edp generate                  # 3 batches of synthetic data into data/landing
edp run                       # ingest, model, quality gate
edp report                    # reports/kpi_report.md + CSVs
```

## CLI reference

| Command | What it does | Exit codes |
|---|---|---|
| `edp migrate` | applies pending migrations | 0 ok, 1 migration drift, 2 config |
| `edp generate [--seed N] [--batches N] [--days-per-batch N] [--orders-per-day N] [--shipments-per-day N] [--defect-rate F] [--no-schema-drift] [--output DIR]` | writes synthetic landing files and `_generation_manifest.json` | 0 |
| `edp run [--landing-dir DIR] [--full-refresh]` | ingests new files and refreshes marts | 0 ok, 1 failed, 2 config, 3 another run in progress |
| `edp report [--output DIR]` | writes KPI CSVs and a Markdown report | 0 |

## Configuration

All settings are environment variables. See [`.env.example`](../.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `EDP_DATABASE_URL` | — (required) | PostgreSQL connection string |
| `EDP_LANDING_DIR` | `data/landing` | folder scanned for source files |
| `EDP_REPORT_DIR` | `reports` | report output folder |
| `EDP_SOURCE_TIMEZONE` | `Europe/London` | applied to naive timestamps; also the business-date timezone |
| `EDP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `EDP_LOG_FORMAT` | `json` | `json` (one object per line) or `text` |
| `EDP_SCHEMA_DRIFT_POLICY` | `warn` | `warn` loads files with extra columns; `reject` refuses them |
| `EDP_MAX_REJECT_RATE` | `0.25` | reject the whole file above this share of invalid rows |
| `EDP_MAX_RETRIES` | `3` | attempts per file / model refresh for transient DB errors |
| `EDP_RETRY_BASE_DELAY_S` | `0.5` | base for exponential backoff (capped at 10 s) |

## Loading your own files

Drop files named `<entity>__<batch>.<ext>` into the landing folder. `<entity>` is one of `suppliers`, `warehouses`, `products`, `production_orders`, `inventory_movements` or `shipments`, and `<ext>` is `csv`, `json` (array, or `{"records": [...]}`) or `jsonl`. Batch names should sort chronologically, for example `batch_2026_04_01`. The expected columns are listed in the [data dictionary](data-dictionary.md).
