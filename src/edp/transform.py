"""Incremental refresh of the analytical star schema.

Each model reads only staging rows whose ``_change_seq`` is above the model's
stored watermark and at or below the current maximum, upserts them, and then
advances the watermark. All models refresh in one transaction, so the marts
and their watermarks always move together: a failure leaves both untouched
and the next run picks up exactly where the last successful one stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import psycopg
from psycopg import sql

from edp.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Model:
    name: str
    source_table: str
    sql_file: str

    def load_sql(self) -> str:
        return resources.files("edp").joinpath("sql", "transform", self.sql_file).read_text("utf-8")


# Dimensions first: facts resolve surrogate keys from them.
MODELS: tuple[Model, ...] = (
    Model("dim_supplier", "suppliers", "10_dim_supplier.sql"),
    Model("dim_warehouse", "warehouses", "11_dim_warehouse.sql"),
    Model("dim_product", "products", "12_dim_product.sql"),
    Model("fact_production_order", "production_orders", "20_fact_production_order.sql"),
    Model("fact_inventory_movement", "inventory_movements", "21_fact_inventory_movement.sql"),
    Model("fact_shipment", "shipments", "22_fact_shipment.sql"),
)

_MART_TABLES = [m.name for m in reversed(MODELS)]


@dataclass(frozen=True)
class ModelResult:
    name: str
    low: int
    high: int
    rows: int


def refresh_models(
    conn: psycopg.Connection, business_timezone: str, *, full_refresh: bool = False
) -> list[ModelResult]:
    """Refresh every model. Must be called inside a transaction."""
    if full_refresh:
        tables = sql.SQL(", ").join(sql.Identifier("mart", t) for t in _MART_TABLES)
        conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(tables))
        conn.execute("DELETE FROM ops.model_watermarks")
        log.info("full_refresh_reset")

    results = []
    for model in MODELS:
        row = conn.execute(
            "SELECT last_change_seq FROM ops.model_watermarks WHERE model_name = %s FOR UPDATE",
            (model.name,),
        ).fetchone()
        low = int(row[0]) if row else 0
        high_row = conn.execute(
            sql.SQL("SELECT coalesce(max(_change_seq), 0) FROM staging.{}").format(
                sql.Identifier(model.source_table)
            )
        ).fetchone()
        high = int(high_row[0]) if high_row else 0
        rows = 0
        if high > low:
            cur = conn.execute(
                model.load_sql(),
                {"low": low, "high": high, "tz": business_timezone},
            )
            rows = max(cur.rowcount, 0)
        conn.execute(
            """
            INSERT INTO ops.model_watermarks (model_name, last_change_seq, rows_affected)
            VALUES (%s, %s, %s)
            ON CONFLICT (model_name) DO UPDATE SET
                last_change_seq = EXCLUDED.last_change_seq,
                rows_affected = EXCLUDED.rows_affected,
                updated_at = now()
            """,
            (model.name, max(high, low), rows),
        )
        results.append(ModelResult(model.name, low, high, rows))
        log.info(
            "model_refreshed",
            extra={"fields": {"model": model.name, "from_seq": low, "to_seq": high, "rows": rows}},
        )
    return results
