"""Dataset-level data-quality checks that gate publication of the marts.

These run inside the model-refresh transaction, after the marts are updated
but before commit. If any ``error`` check fails, the transaction is rolled
back, so consumers never see marts that disagree with staging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


class QualityGateError(RuntimeError):
    """One or more blocking data-quality checks failed."""

    def __init__(self, results: list[CheckResult]) -> None:
        self.results = results
        self.failures = [r for r in results if r.blocking_failure]
        names = ", ".join(f.name for f in self.failures)
        super().__init__(f"blocking data-quality checks failed: {names}")


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: str
    passed: bool
    observed: float
    threshold: float | None = None
    entity: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking_failure(self) -> bool:
        return self.severity == "error" and not self.passed


# (check name, entity, staging table, mart table)
_RECONCILIATIONS = [
    ("reconcile_suppliers", "suppliers", "suppliers", "dim_supplier"),
    ("reconcile_warehouses", "warehouses", "warehouses", "dim_warehouse"),
    ("reconcile_products", "products", "products", "dim_product"),
    (
        "reconcile_production_orders",
        "production_orders",
        "production_orders",
        "fact_production_order",
    ),
    (
        "reconcile_inventory_movements",
        "inventory_movements",
        "inventory_movements",
        "fact_inventory_movement",
    ),
    ("reconcile_shipments", "shipments", "shipments", "fact_shipment"),
]


def run_dataset_checks(conn: psycopg.Connection, run_quarantine_rate: float) -> list[CheckResult]:
    results: list[CheckResult] = []

    for name, entity, staging_table, mart_table in _RECONCILIATIONS:
        row = conn.execute(
            sql.SQL("SELECT (SELECT count(*) FROM {}), (SELECT count(*) FROM {})").format(
                sql.Identifier("staging", staging_table), sql.Identifier("mart", mart_table)
            )
        ).fetchone()
        staged, modelled = (int(row[0]), int(row[1])) if row else (0, 0)
        results.append(
            CheckResult(
                name,
                "error",
                staged == modelled,
                observed=modelled - staged,
                threshold=0,
                entity=entity,
                details={"staging_rows": staged, "mart_rows": modelled},
            )
        )

    row = conn.execute(
        "SELECT (SELECT coalesce(sum(quantity_delta), 0) FROM staging.inventory_movements), "
        "(SELECT coalesce(sum(quantity_delta), 0) FROM mart.fact_inventory_movement)"
    ).fetchone()
    staged_units, modelled_units = (int(row[0]), int(row[1])) if row else (0, 0)
    results.append(
        CheckResult(
            "reconcile_stock_units",
            "error",
            staged_units == modelled_units,
            observed=modelled_units - staged_units,
            threshold=0,
            entity="inventory_movements",
            details={"staging_units": staged_units, "mart_units": modelled_units},
        )
    )

    row = conn.execute(
        "SELECT count(*) FROM mart.v_inventory_position WHERE on_hand < 0"
    ).fetchone()
    negative = int(row[0]) if row else 0
    results.append(
        CheckResult(
            "negative_stock_positions",
            "warning",
            negative == 0,
            observed=negative,
            threshold=0,
            entity="inventory_movements",
        )
    )

    row = conn.execute(
        "SELECT count(*) FROM mart.fact_shipment WHERE status = 'delivered' AND lead_time_days < 0"
    ).fetchone()
    impossible = int(row[0]) if row else 0
    results.append(
        CheckResult(
            "negative_lead_times",
            "error",
            impossible == 0,
            observed=impossible,
            threshold=0,
            entity="shipments",
        )
    )

    results.append(
        CheckResult(
            "run_quarantine_rate",
            "warning",
            run_quarantine_rate <= 0.05,
            observed=round(run_quarantine_rate, 4),
            threshold=0.05,
        )
    )
    return results


def record_checks(conn: psycopg.Connection, run_id: str, results: list[CheckResult]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ops.dq_results
                (run_id, check_name, entity, severity, passed, observed_value, threshold, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    run_id,
                    r.name,
                    r.entity,
                    r.severity,
                    r.passed,
                    r.observed,
                    r.threshold,
                    Jsonb(r.details) if r.details else None,
                )
                for r in results
            ],
        )
