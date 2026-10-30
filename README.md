# enterprise-data-pipeline

Batch data pipeline for manufacturing and supply-chain analytics, built with Python and PostgreSQL.

> **Status:** the ingestion and data-quality layer is in place. Analytical models, KPI reporting and full documentation follow in later pull requests.

## What it does today

- **Ingests** CSV, JSON and JSON Lines files from a landing folder into PostgreSQL.
- **Validates** every record against a data contract: required fields, types, value ranges, impossible dates, negative quantities, broken foreign keys and cross-field rules.
- **Quarantines** bad records with their original payload and every reason code, instead of dropping them silently.
- **Loads incrementally and idempotently.** A file manifest keyed by SHA-256 means re-running never reloads a file. Record-level upserts only overwrite with a newer `updated_at`, so out-of-order files cannot roll data back.
- **Recovers from failures.** Each file loads in a single transaction. Transient database errors are retried with backoff, and an advisory lock stops two runs from executing at once.
- **Generates realistic synthetic data** with a known number of injected defects, so the tests can prove that every defect is caught.

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install --no-deps -e .
cp .env.example .env   # then set EDP_DATABASE_URL to a PostgreSQL 16 database
set -a && source .env && set +a

edp generate        # writes synthetic files to data/landing
edp run             # applies migrations, then ingests new files
```

## Tests

```bash
export EDP_TEST_DATABASE_URL=postgresql://edp:<password>@localhost:5432/edp_test
pytest
```

Integration tests drop and recreate the schemas in `EDP_TEST_DATABASE_URL`, so point it at a disposable database. CI runs the full suite against PostgreSQL 16 on Python 3.11 and 3.12. CI fails the build if any test is skipped.

## Licence

[MIT](LICENSE)
