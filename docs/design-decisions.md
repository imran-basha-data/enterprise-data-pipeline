# Design decisions

Short records of the main choices, the alternatives considered, and the trade-offs.

## 1. Plain Python + SQL instead of an orchestration or transformation framework

**Decision:** a small Python package (psycopg 3) with SQL files for transformations. No Airflow, dbt or Spark.

**Why:** the goal is to show the mechanics those tools hide: idempotent loads, watermarks, transactional publishing and quality gates. Each mechanism here maps directly to a feature of the bigger tools: watermarks ≈ dbt incremental models; dataset checks ≈ dbt tests or Great Expectations; the advisory lock ≈ Airflow `max_active_runs=1`.

**Trade-off:** no scheduler, UI or lineage graph. Scheduling is left to cron, Kubernetes CronJobs or an orchestrator calling `edp run`.

## 2. File-level idempotency by content hash

**Decision:** the manifest is unique on `(entity, sha256)`, not on file name.

**Why:** upstream systems re-send files, rename them, or deliver duplicates. Hashing content makes "have I seen this?" exact and cheap.

**Trade-off:** a file with one changed row is a new file. That is correct, because record-level logic then handles the change.

## 3. Version-guarded upserts

**Decision:** mutable entities only overwrite when the incoming `updated_at` is strictly newer. Events are insert-only.

**Why:** late or out-of-order files must never roll data back. The guard sits in the SQL (`WHERE target.updated_at < EXCLUDED.updated_at`) as well as in Python classification, so it holds even if the Python logic were wrong.

**Trade-off:** a source that sends a correction without bumping `updated_at` is treated as a conflict and quarantined. That is deliberate: silent overwrites hide upstream bugs.

## 4. Quarantine, not drop, and reject whole files above a threshold

**Decision:** invalid rows go to `ops.quarantine` with every reason and the original payload. A file with more than 25% invalid rows is rejected outright.

**Why:** dropping rows silently destroys trust. Loading a file that is mostly garbage usually means the export itself is broken, and partial loads of broken exports corrupt KPIs.

## 5. One transaction per file; one transaction for all models

**Decision:** each file is atomic. The model refresh and its quality gate are atomic together.

**Why:** a crash can only lose the file in flight, which the next run reloads. Consumers never see half-refreshed marts or marts that disagree with staging.

**Trade-off:** a very large file holds one long transaction. For much larger volumes, split files upstream or move to staged micro-batches.

## 6. Change-sequence watermarks instead of timestamps

**Decision:** a PostgreSQL sequence stamps every staging write. Models track the highest value consumed.

**Why:** timestamp watermarks miss rows when clocks skew or when transactions commit out of order. A single writer (enforced by the advisory lock) plus a sequence gives a gap-free, monotonic bookmark.

## 7. Type-1 dimensions with stable surrogate keys

**Decision:** dimensions hold current state. Surrogate keys never change on update.

**Why:** the KPIs use current attributes (current unit cost for stock value). SCD2 would add complexity without a requirement driving it.

**Trade-off:** historical reports reflect *current* product cost and supplier lead time. See Known limitations in the README.

## 8. Measure "overdue" against the data's as-of date

**Decision:** open-item KPIs compare against `max(event time in the data)`, not `now()`.

**Why:** reports are then reproducible. Re-running last month's data gives last month's answer, and synthetic data from the past doesn't show everything as overdue.

## 9. Synthetic data with a defect manifest

**Decision:** the generator injects a known number of each defect type and records the counts.

**Why:** a data-quality pipeline is only as good as the evidence that it catches problems. The end-to-end test asserts quarantine counts by reason **equal** the manifest, which catches both missed defects and false positives.

## 10. No cloud warehouse adapters

**Decision:** PostgreSQL only.

**Why:** a Snowflake or BigQuery adapter could not be tested here without paid accounts, and an untested adapter would be a claim without evidence. The SQL is mostly ANSI. The PostgreSQL-specific parts are `ON CONFLICT`, advisory locks, `COPY` and JSONB, so they are the places a port would need to change (to `MERGE`, warehouse transactions and staged loads).
