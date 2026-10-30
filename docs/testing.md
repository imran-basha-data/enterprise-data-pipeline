# Testing

## Running the tests

```bash
export EDP_TEST_DATABASE_URL=postgresql://edp:<password>@localhost:5432/edp_test
pytest                      # everything
pytest tests/unit           # no database needed
pytest -m integration       # database tests only
pytest --cov                # with branch coverage
```

The integration suite **drops and recreates** the `ops`, `raw`, `staging` and `mart` schemas before every test, so use a disposable database.

If `EDP_TEST_DATABASE_URL` is unset, database tests are skipped locally. CI sets `EDP_REQUIRE_DB=1`, which turns a missing database into a failure. It also parses the JUnit report and fails the build if *any* test was skipped.

## What is tested

| Area | Examples | File |
|---|---|---|
| Parsing | null tokens, int32 bounds, UK dates, impossible dates, BST conversion, epoch ms | `tests/unit/test_parsing.py` |
| Validation | each reason code, aliases, defaults, cross-field rules, no cascading errors, content hash ignores cosmetic differences | `tests/unit/test_validation.py` |
| Readers | dependency ordering, BOM, ragged CSV rows, malformed JSONL lines, unreadable JSON, schema drift detection | `tests/unit/test_readers.py` |
| Config / retry / logging | invalid settings, DSN redaction, backoff schedule, no retry on permanent errors, JSON log shape | `tests/unit/test_config_retry_logging.py` |
| Generator | determinism, manifest accuracy, drift only in configured batch | `tests/unit/test_generator.py` |
| Ingestion (DB) | typed load, idempotent rerun, same content under a new name, incremental update, out-of-order delivery, in-file duplicates and conflicts, cross-file event conflict, orphans, row accounting, schema drift warn/reject, missing column, unreadable file, reject-rate breaker, payload retention | `tests/integration/test_ingestion.py` |
| Reliability (DB) | crash mid-file then recover to the same state as a clean run, transient error retried without duplicates, retries exhausted, concurrent run blocked, abandoned runs, migration tamper detection | `tests/integration/test_reliability.py` |
| End to end (DB) | quarantine counts **exactly** match the generator's injected-defect manifest; full rerun is a no-op | `tests/integration/test_generated_dataset.py` |
| Models and KPIs (DB) | every KPI against **hand-calculated** values, incremental == full refresh, quality gate rollback, SCD1 key stability, timezone date keys, report output | `tests/integration/test_models_and_reporting.py` |
| CLI (DB) | generate → migrate → run, config error exit code, lock exit code | `tests/integration/test_cli.py` |

### Failure injection

`run_pipeline(..., checkpoint=fn)` calls `fn("after_raw")` and `fn("after_staging")` inside each file's transaction. Tests use it to raise a crash or a `psycopg.OperationalError` at a precise point, then assert what was and was not committed. This hook has no effect in normal runs.

## Results

Local run, PostgreSQL 16, Python 3.12:

```text
127 passed in 16.58s
TOTAL   1705 statements   96% branch coverage
```

CI runs the same suite on Python 3.11 and 3.12. Each PR description records its pass/fail/skip counts from CI.

## Static and security checks

| Tool | Command | Gate |
|---|---|---|
| Ruff | `ruff check src tests && ruff format --check src tests` | CI |
| Mypy | `mypy` (strict) | CI |
| Bandit | `bandit -r src -c pyproject.toml` | CI |
| pip-audit | `pip-audit -r requirements.txt --strict` | CI |
| Gitleaks | full git history scan | CI |
| Docker | image builds, runs as non-root, Compose end-to-end run, idempotent rerun | CI |

## Performance observation

On the development machine (4 vCPU, local PostgreSQL 16), `edp run` over the default generated dataset (15 files, 7,770 rows) took about 1.4 s wall-clock, including migrations, ingestion, model refresh and checks. That is a single observation on a small dataset, not a benchmark. Bulk paths use `COPY` and set-based upserts, but the pipeline has not been load-tested at production volumes.
