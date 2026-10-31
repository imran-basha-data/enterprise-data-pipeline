# enterprise-data-pipeline

Batch data pipeline for manufacturing and supply-chain analytics, built with Python and PostgreSQL.

> **Status:** ingestion, data quality, the analytical star schema, KPI reporting and Docker packaging are in place. Full documentation follows in the next pull request.

## What it does today

- **Ingests** CSV, JSON and JSON Lines files from a landing folder into PostgreSQL.
- **Validates** every record against a data contract: required fields, types, value ranges, impossible dates, negative quantities, broken foreign keys and cross-field rules.
- **Quarantines** bad records with their original payload and every reason code, instead of dropping them silently.
- **Loads incrementally and idempotently.** A file manifest keyed by SHA-256 means re-running never reloads a file. Record-level upserts only overwrite with a newer `updated_at`, so out-of-order files cannot roll data back.
- **Recovers from failures.** Each file loads in a single transaction. Transient database errors are retried with backoff, and an advisory lock stops two runs from executing at once.
- **Models** staging data into a star schema (`mart.dim_*`, `mart.fact_*`) incrementally, using per-model change-sequence watermarks. A **quality gate** reconciles marts with staging and rolls the refresh back if they disagree.
- **Reports KPIs:** production plan attainment, scrap and on-time completion; supplier OTIF, lead time and defect rate; shipment delays; inventory position and days of cover. Output is CSV plus a Markdown summary.
- **Generates realistic synthetic data** with a known number of injected defects, so the tests can prove that every defect is caught.

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install --no-deps -e .
cp .env.example .env   # then set EDP_DATABASE_URL to a PostgreSQL 16 database
set -a && source .env && set +a

edp generate        # writes synthetic files to data/landing
edp run             # applies migrations, ingests new files, refreshes marts
edp report          # writes reports/kpi_report.md and CSV extracts
```

Or with Docker only:

```bash
cp .env.example .env && mkdir -p data/landing reports   # set POSTGRES_PASSWORD; on Linux set HOST_UID/HOST_GID
docker compose --profile demo up --exit-code-from demo demo
```

## Tests

```bash
export EDP_TEST_DATABASE_URL=postgresql://edp:<password>@localhost:5432/edp_test
pytest
```

Integration tests drop and recreate the schemas in `EDP_TEST_DATABASE_URL`, so point it at a disposable database. CI runs the full suite against PostgreSQL 16 on Python 3.11 and 3.12. CI fails the build if any test is skipped.

## Licence

[MIT](LICENSE)
