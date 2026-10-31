"""KPI reporting: CSV extracts and a Markdown summary from the KPI views."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg


@dataclass(frozen=True)
class Section:
    slug: str
    title: str
    description: str
    query: str
    preview_rows: int | None = None


SECTIONS: tuple[Section, ...] = (
    Section(
        "operational_kpis",
        "Operational summary",
        "Headline KPIs across production, procurement, inventory and data quality.",
        "SELECT * FROM mart.v_operational_kpis",
    ),
    Section(
        "production_by_month",
        "Production performance by month and category",
        "Completed orders only. Plan attainment = produced / planned units; scrap rate = scrap / "
        "(produced + scrap); on time = finished no later than the planned end.",
        """
        SELECT to_char(d.calendar_date, 'YYYY-MM') AS month, p.category,
               count(*) AS orders_completed,
               sum(f.produced_quantity) AS produced_units,
               round(100.0 * sum(f.produced_quantity) / nullif(sum(f.planned_quantity), 0), 2)
                   AS plan_attainment_pct,
               round(100.0 * sum(f.scrap_quantity)
                     / nullif(sum(f.produced_quantity + f.scrap_quantity), 0), 2) AS scrap_rate_pct,
               round(100.0 * avg(f.is_on_time::integer), 2) AS on_time_completion_pct,
               round(avg(f.actual_hours), 1) AS avg_cycle_hours
        FROM mart.fact_production_order f
        JOIN mart.dim_date d ON d.date_key = f.completed_date_key
        JOIN mart.dim_product p ON p.product_key = f.product_key
        WHERE f.is_completed
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
    ),
    Section(
        "supplier_scorecard",
        "Supplier scorecard",
        "OTIF = delivered on or before the promised date AND accepted quantity >= 98% of ordered. "
        "Sorted from worst to best OTIF.",
        "SELECT * FROM mart.v_supplier_scorecard ORDER BY otif_pct NULLS FIRST, supplier_id",
    ),
    Section(
        "shipment_delays",
        "Shipment delay distribution",
        "Delivered shipments by days late against the promised date.",
        "SELECT delay_bucket, shipments, share_pct FROM mart.v_shipment_delays "
        "ORDER BY bucket_order",
    ),
    Section(
        "inventory_risk",
        "Inventory below reorder point",
        "Stock positions below their reorder point, lowest cover first. Days of cover uses the "
        "average daily issue over the last 30 days.",
        """
        SELECT product_id, product_name, warehouse_id, on_hand, reorder_point,
               avg_daily_issue_30d, days_of_cover, is_stockout
        FROM mart.v_inventory_position
        WHERE is_below_reorder_point
        ORDER BY is_stockout DESC, days_of_cover NULLS LAST, on_hand, product_id, warehouse_id
        """,
        preview_rows=15,
    ),
    Section(
        "data_quality",
        "Data quality: quarantined records",
        "Records rejected during ingestion, by entity and reason. A record can carry more than "
        "one reason.",
        """
        SELECT q.entity, code AS reason, count(*) AS records
        FROM ops.quarantine q, unnest(q.reason_codes) AS code
        GROUP BY 1, 2 ORDER BY 1, 3 DESC, 2
        """,
    ),
)


def generate_report(dsn: str, output_dir: Path) -> Path:
    """Write one CSV per section plus ``kpi_report.md``; return the Markdown path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Manufacturing and supply-chain KPI report", ""]
    with psycopg.connect(dsn) as conn:
        as_of = conn.execute("SELECT as_of_date FROM mart.v_data_as_of").fetchone()
        lines += [f"Data as of **{_fmt(as_of[0] if as_of else None)}**.", ""]
        for section in SECTIONS:
            cur = conn.execute(section.query)
            columns = [c.name for c in cur.description or []]
            rows = cur.fetchall()
            _write_csv(output_dir / f"{section.slug}.csv", columns, rows)
            lines += [f"## {section.title}", "", section.description, ""]
            if section.slug == "operational_kpis" and rows:
                lines += _key_value_table(columns, rows[0])
            else:
                shown = rows[: section.preview_rows] if section.preview_rows else rows
                lines += _table(columns, shown)
                if section.preview_rows and len(rows) > section.preview_rows:
                    lines.append(
                        f"\n_Showing {section.preview_rows} of {len(rows)} rows; "
                        f"see `{section.slug}.csv` for all._"
                    )
            lines.append("")
    report = output_dir / "kpi_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return f"{value:,.2f}" if value != value.to_integral_value() else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value).replace("|", "\\|")


def _table(columns: list[str], rows: list[tuple[Any, ...]]) -> list[str]:
    if not rows:
        return ["_No rows._"]
    out = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    out += ["| " + " | ".join(_fmt(v) for v in row) + " |" for row in rows]
    return out


def _key_value_table(columns: list[str], row: tuple[Any, ...]) -> list[str]:
    out = ["| KPI | Value |", "|---|---|"]
    out += [f"| {c} | {_fmt(v)} |" for c, v in zip(columns, row, strict=True)]
    return out


def _write_csv(path: Path, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
