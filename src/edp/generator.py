"""Deterministic synthetic manufacturing and supply-chain data.

The generator simulates a small UK manufacturer: suppliers deliver components
to five warehouses, three of which host production lines; production orders
consume capacity and create finished stock; warehouses issue, transfer and
count stock daily. Each batch covers a fixed window and is written in the
formats real source systems tend to use:

* reference data (suppliers, warehouses, products) as CSV snapshots/deltas;
* production orders as CSV from an MES with *naive local* timestamps;
* inventory movements as JSON Lines from a WMS, with mixed timestamp formats;
* shipments as a JSON array from a procurement system.

Records that change status (orders, shipments) are re-sent in later batches
with a newer ``updated_at``, which exercises incremental upserts.

A configurable share of records is corrupted with known defects. The exact
number of each injected defect is written to ``_generation_manifest.json`` so
tests can assert that the pipeline caught every one of them.
"""

from __future__ import annotations

import csv
import json
import random
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from edp.contracts import (
    INVENTORY_MOVEMENTS,
    PRODUCTION_ORDERS,
    PRODUCTS,
    SHIPMENTS,
    SUPPLIERS,
    WAREHOUSES,
    Reason,
)

LONDON = ZoneInfo("Europe/London")
MANIFEST_NAME = "_generation_manifest.json"


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    batches: int = 3
    days_per_batch: int = 30
    start_date: date = date(2026, 1, 5)
    orders_per_day: int = 12
    shipments_per_day: int = 8
    defect_rate: float = 0.02
    duplicate_rate: float = 0.005
    schema_drift_batch: int | None = 3

    def __post_init__(self) -> None:
        if self.batches < 1:
            raise ValueError("batches must be >= 1")
        if not 0 <= self.defect_rate < 0.5:
            raise ValueError("defect_rate must be in [0, 0.5)")
        if not 0 <= self.duplicate_rate < 0.2:
            raise ValueError("duplicate_rate must be in [0, 0.2)")


# --------------------------------------------------------------------------
# Master data (all names are fictitious)
# --------------------------------------------------------------------------

_SUPPLIERS: list[tuple[str, str, str, int, float, float, tuple[str, ...]]] = [
    # id, name, country, lead days, on-time probability, defect rate, categories
    ("SUP-001", "Northgate Precision Castings", "GB", 7, 0.93, 0.010, ("Hydraulic Components",)),
    ("SUP-002", "Calder Valley Fasteners", "GB", 5, 0.97, 0.004, ("Fasteners",)),
    ("SUP-003", "Brightwater Electronics", "GB", 10, 0.82, 0.020, ("Electrical Assemblies",)),
    ("SUP-004", "Pennine Polymers", "GB", 6, 0.90, 0.012, ("Raw Materials",)),
    ("SUP-005", "Severn Hydraulic Systems", "GB", 9, 0.78, 0.025, ("Hydraulic Components",)),
    ("SUP-006", "Tynebridge Sensor Works", "GB", 8, 0.88, 0.015, ("Sensors",)),
    (
        "SUP-007",
        "Lindenhof Komponenten",
        "DE",
        14,
        0.91,
        0.006,
        ("Electrical Assemblies", "Sensors"),
    ),
    ("SUP-008", "Iberia Alloys", "ES", 16, 0.72, 0.018, ("Raw Materials",)),
    ("SUP-009", "Vistula Cable Works", "PL", 12, 0.85, 0.011, ("Electrical Assemblies",)),
    ("SUP-010", "Ashby Metal Pressings", "GB", 6, 0.95, 0.008, ("Fasteners", "Raw Materials")),
    (
        "SUP-011",
        "Kestrel Industrial Supply",
        "IE",
        9,
        0.80,
        0.022,
        ("Hydraulic Components", "Fasteners"),
    ),
    ("SUP-012", "Brabant Measurement Systems", "NL", 11, 0.89, 0.009, ("Sensors",)),
]

_WAREHOUSES: list[tuple[str, str, str, str, int]] = [
    ("WH-BHM", "Birmingham Central Plant", "Birmingham", "West Midlands", 120_000),
    ("WH-MAN", "Manchester Assembly Plant", "Manchester", "North West", 90_000),
    ("WH-LDS", "Leeds Components Plant", "Leeds", "Yorkshire", 75_000),
    ("WH-GLA", "Glasgow Distribution Centre", "Glasgow", "Scotland", 60_000),
    ("WH-BRS", "Bristol Distribution Centre", "Bristol", "South West", 55_000),
]
_PLANTS = ("WH-BHM", "WH-MAN", "WH-LDS")

_CATEGORIES: dict[str, tuple[str, str, tuple[float, float], list[str]]] = {
    # category: (sku prefix, unit of measure, cost range, base names)
    "Hydraulic Components": (
        "HYD",
        "EA",
        (45, 420),
        ["Hydraulic Pump", "Pressure Relief Valve", "Hydraulic Cylinder", "Hose Assembly"],
    ),
    "Electrical Assemblies": (
        "ELE",
        "EA",
        (30, 650),
        ["Control Panel", "Wiring Harness", "Motor Controller", "Power Supply Unit"],
    ),
    "Fasteners": (
        "FST",
        "BOX",
        (4, 35),
        ["Hex Bolt M12", "Lock Nut M10", "Washer Set", "Anchor Bolt"],
    ),
    "Sensors": (
        "SNS",
        "EA",
        (18, 260),
        ["Proximity Sensor", "Temperature Probe", "Flow Meter", "Pressure Transducer"],
    ),
    "Raw Materials": (
        "RAW",
        "KG",
        (2, 18),
        ["Steel Sheet 2mm", "Aluminium Bar", "Copper Wire", "Polymer Granulate"],
    ),
}
_FINISHED_GOODS = ("Hydraulic Components", "Electrical Assemblies", "Sensors")
_CARRIERS = ("DHL Freight", "DPD", "Kuehne Logistics", "XPO", "Royal Mail Parcelforce")


@dataclass
class _Product:
    product_id: str
    sku: str
    name: str
    category: str
    uom: str
    unit_cost: Decimal
    reorder_point: int
    supplier_id: str
    updated_at: datetime


@dataclass
class _Order:
    order_id: str
    product_id: str
    warehouse_id: str
    planned_quantity: int
    planned_start: datetime
    planned_end: datetime
    actual_start: datetime
    actual_end: datetime
    produced: int
    scrap: int
    cancelled: bool
    last_status: str | None = None

    def status_at(self, cutoff: datetime) -> str:
        if self.cancelled:
            return "cancelled"
        if self.actual_start >= cutoff:
            return "planned"
        if self.actual_end >= cutoff:
            return "in_progress"
        return "completed"


@dataclass
class _Shipment:
    shipment_id: str
    supplier_id: str
    product_id: str
    warehouse_id: str
    quantity_ordered: int
    order_date: date
    promised_date: date
    shipped_at: datetime
    delivered_at: datetime
    received: int
    rejected: int
    carrier: str
    cancelled: bool
    last_status: str | None = None

    @property
    def created_at(self) -> datetime:
        return datetime.combine(self.order_date, time(9, 0), tzinfo=UTC)

    def status_at(self, cutoff: datetime) -> str:
        if self.cancelled:
            return "cancelled"
        if self.shipped_at >= cutoff:
            return "ordered"
        if self.delivered_at >= cutoff:
            return "in_transit"
        return "delivered"


@dataclass
class FileManifest:
    file: str
    entity: str
    rows_written: int
    injected: dict[str, int] = field(default_factory=dict)
    exact_duplicates: int = 0


@dataclass
class GenerationManifest:
    seed: int
    batches: int
    files: list[FileManifest] = field(default_factory=list)

    def expected_quarantine(self) -> dict[str, int]:
        totals: Counter[str] = Counter()
        for f in self.files:
            totals.update(f.injected)
        return dict(totals)

    def expected_exact_duplicates(self) -> int:
        return sum(f.exact_duplicates for f in self.files)

    def to_json(self) -> str:
        return json.dumps(
            {
                "seed": self.seed,
                "batches": self.batches,
                "expected_quarantine_by_reason": self.expected_quarantine(),
                "expected_exact_duplicates": self.expected_exact_duplicates(),
                "files": [f.__dict__ for f in self.files],
            },
            indent=2,
        )


class _Generator:
    def __init__(self, config: GeneratorConfig, output_dir: Path) -> None:
        self.cfg = config
        self.out = output_dir
        # Seeded PRNG: reproducible synthetic data, not a security use.
        self.rng = random.Random(config.seed)  # nosec B311
        self.manifest = GenerationManifest(seed=config.seed, batches=config.batches)
        self.products: list[_Product] = []
        self.supplier_lead: dict[str, int] = {s[0]: s[3] for s in _SUPPLIERS}
        self.orders: list[_Order] = []
        self.shipments: list[_Shipment] = []
        self.on_hand: dict[tuple[str, str], int] = defaultdict(int)
        self.order_seq = 0
        self.shipment_seq = 0

    # ---------------------------------------------------------------- utils
    def _uuid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def _window(self, batch: int) -> tuple[datetime, datetime]:
        start = datetime.combine(
            self.cfg.start_date + timedelta(days=(batch - 1) * self.cfg.days_per_batch),
            time(0, 0),
            tzinfo=UTC,
        )
        return start, start + timedelta(days=self.cfg.days_per_batch)

    # ---------------------------------------------------------- master data
    def _build_products(self, as_of: datetime) -> None:
        number = 1
        for category, (prefix, uom, (lo, hi), names) in _CATEGORIES.items():
            suppliers = [s[0] for s in _SUPPLIERS if category in s[6]]
            for base in names:
                initials = "".join(word[0] for word in base.split()).upper()
                for variant in (100, 200):
                    self.products.append(
                        _Product(
                            product_id=f"PRD-{number:04d}",
                            sku=f"{prefix}-{initials}-{variant}",
                            name=f"{base} {initials}-{variant}",
                            category=category,
                            uom=uom,
                            unit_cost=Decimal(str(round(self.rng.uniform(lo, hi), 2))),
                            reorder_point=self.rng.choice([50, 80, 120, 200, 300]),
                            supplier_id=self.rng.choice(suppliers),
                            updated_at=as_of,
                        )
                    )
                    number += 1

    def _supplier_rows(self, as_of: datetime, only: set[str] | None = None) -> list[dict[str, Any]]:
        rows = []
        for sid, name, country, _lead, _p, _d, _cats in _SUPPLIERS:
            if only is not None and sid not in only:
                continue
            rows.append(
                {
                    "supplier_id": sid,
                    "supplier_name": name,
                    "country_code": country,
                    "contracted_lead_time_days": self.supplier_lead[sid],
                    "is_active": "true",
                    "updated_at": as_of.isoformat(),
                }
            )
        return rows

    def _product_row(self, p: _Product) -> dict[str, Any]:
        name = f"  {p.name} " if self.rng.random() < 0.1 else p.name  # cosmetic noise
        return {
            "product_id": p.product_id,
            "sku": p.sku,
            "product_name": name,
            "category": p.category,
            "unit_of_measure": p.uom,
            "unit_cost": f"{p.unit_cost:.2f}",
            "reorder_point": p.reorder_point,
            "primary_supplier_id": p.supplier_id,
            "is_active": "true",
            "updated_at": p.updated_at.isoformat(),
        }

    # ---------------------------------------------------------- simulation
    def _new_orders(self, day: datetime) -> None:
        finished = [p for p in self.products if p.category in _FINISHED_GOODS]
        for _ in range(max(0, self.cfg.orders_per_day + self.rng.randint(-3, 3))):
            self.order_seq += 1
            product = self.rng.choice(finished)
            planned_qty = self.rng.choice([20, 40, 50, 80, 100, 150, 200, 250])
            planned_start = day + timedelta(hours=6 + self.rng.randint(0, 12))
            duration = timedelta(hours=self.rng.randint(4, 36))
            delay = timedelta(minutes=int(max(-120, min(600, self.rng.gauss(15, 90)))))
            actual_start = planned_start + delay
            actual_end = actual_start + duration * self.rng.uniform(0.85, 1.2)
            actual_end = actual_end.replace(microsecond=0, second=0)
            self.orders.append(
                _Order(
                    order_id=f"MO-{day.year}-{self.order_seq:06d}",
                    product_id=product.product_id,
                    warehouse_id=self.rng.choice(_PLANTS),
                    planned_quantity=planned_qty,
                    planned_start=planned_start,
                    planned_end=planned_start + duration,
                    actual_start=actual_start,
                    actual_end=actual_end,
                    produced=round(planned_qty * self.rng.uniform(0.88, 1.03)),
                    scrap=round(planned_qty * self.rng.uniform(0.0, 0.04)),
                    cancelled=self.rng.random() < 0.03,
                )
            )

    def _new_shipments(self, day: datetime) -> None:
        for _ in range(max(0, self.cfg.shipments_per_day + self.rng.randint(-2, 2))):
            self.shipment_seq += 1
            product = self.rng.choice(self.products)
            supplier = next(s for s in _SUPPLIERS if s[0] == product.supplier_id)
            lead = self.supplier_lead[supplier[0]]
            on_time = self.rng.random() < supplier[4]
            target = lead + (self.rng.randint(-2, 0) if on_time else self.rng.randint(1, 7))
            transit = self.rng.randint(1, 3)
            order_date = day.date()
            shipped_at = datetime.combine(
                order_date + timedelta(days=max(1, target - transit)),
                time(self.rng.randint(7, 15), self.rng.choice([0, 15, 30, 45])),
                tzinfo=UTC,
            )
            delivered_at = shipped_at + timedelta(days=transit, hours=self.rng.randint(0, 6))
            ordered = self.rng.choice([50, 100, 200, 250, 500, 1000])
            received = (
                ordered
                if self.rng.random() < 0.92
                else round(ordered * self.rng.uniform(0.85, 0.99))
            )
            rejected = min(received, round(received * supplier[5] * self.rng.uniform(0.5, 1.6)))
            self.shipments.append(
                _Shipment(
                    shipment_id=f"SHP-{order_date.year}-{self.shipment_seq:06d}",
                    supplier_id=supplier[0],
                    product_id=product.product_id,
                    warehouse_id=self.rng.choice([w[0] for w in _WAREHOUSES]),
                    quantity_ordered=ordered,
                    order_date=order_date,
                    promised_date=order_date + timedelta(days=lead),
                    shipped_at=shipped_at,
                    delivered_at=delivered_at,
                    received=received,
                    rejected=rejected,
                    carrier=self.rng.choice(_CARRIERS),
                    cancelled=self.rng.random() < 0.02,
                )
            )

    def _movement(
        self,
        ts: datetime,
        product_id: str,
        warehouse_id: str,
        mtype: str,
        qty: int,
        ref: str | None,
    ) -> dict[str, Any]:
        roll = self.rng.random()
        stamp: Any
        if roll < 0.6:
            stamp = ts.isoformat().replace("+00:00", "Z")
        elif roll < 0.9:
            stamp = ts.astimezone(LONDON).isoformat()
        else:
            stamp = int(ts.timestamp() * 1000)
        wh = warehouse_id.lower() if self.rng.random() < 0.05 else warehouse_id
        delta = -abs(qty) if mtype in ("issue", "transfer_out") else qty
        self.on_hand[(product_id, warehouse_id)] += delta
        return {
            "movement_id": self._uuid(),
            "product_id": product_id,
            "warehouse_id": wh,
            "movement_type": mtype,
            "quantity": qty,
            "reference_id": ref if ref is not None else "N/A",
            "event_timestamp": stamp,
            "_ts": ts,
        }

    # -------------------------------------------------------------- batches
    def run(self) -> GenerationManifest:
        self.out.mkdir(parents=True, exist_ok=True)
        for batch in range(1, self.cfg.batches + 1):
            self._batch(batch)
        (self.out / MANIFEST_NAME).write_text(self.manifest.to_json(), encoding="utf-8")
        return self.manifest

    def _batch(self, batch: int) -> None:
        start, cutoff = self._window(batch)
        tag = f"batch_{batch:03d}"
        movements: list[dict[str, Any]] = []

        if batch == 1:
            as_of = start - timedelta(days=1)
            self._build_products(as_of)
            self._write_csv(SUPPLIERS.name, tag, SUPPLIERS.column_names, self._supplier_rows(as_of))
            self._write_csv(
                WAREHOUSES.name,
                tag,
                WAREHOUSES.column_names,
                [
                    {
                        "warehouse_id": wid,
                        "warehouse_name": name,
                        "city": city,
                        "region": region,
                        "country_code": "GB",
                        "capacity_units": cap,
                        "is_active": "true",
                        "updated_at": as_of.isoformat(),
                    }
                    for wid, name, city, region, cap in _WAREHOUSES
                ],
            )
            self._write_csv(
                PRODUCTS.name,
                tag,
                PRODUCTS.column_names,
                [self._product_row(p) for p in self.products],
            )
            for p in self.products:
                for wid, *_ in _WAREHOUSES:
                    qty = self.rng.randint(p.reorder_point, p.reorder_point * 4)
                    movements.append(
                        self._movement(
                            as_of + timedelta(hours=12),
                            p.product_id,
                            wid,
                            "adjustment",
                            qty,
                            "OPENING-BALANCE",
                        )
                    )
        else:
            as_of = start + timedelta(hours=6)
            changed = self.rng.sample(self.products, 4)
            for p in changed:
                p.unit_cost = (p.unit_cost * Decimal(str(self.rng.uniform(1.02, 1.08)))).quantize(
                    Decimal("0.01")
                )
                p.updated_at = as_of
            resent = self.rng.sample([p for p in self.products if p not in changed], 2)
            self._write_csv(
                PRODUCTS.name,
                tag,
                PRODUCTS.column_names,
                [self._product_row(p) for p in changed + resent],
            )
            if batch == 2:
                sid = "SUP-008"
                self.supplier_lead[sid] += 2
                self._write_csv(
                    SUPPLIERS.name, tag, SUPPLIERS.column_names, self._supplier_rows(as_of, {sid})
                )

        for offset in range(self.cfg.days_per_batch):
            day = start + timedelta(days=offset)
            self._new_orders(day)
            self._new_shipments(day)

        order_rows = self._order_rows(cutoff, movements)
        shipment_rows = self._shipment_rows(cutoff, movements)
        movements.extend(self._daily_operations(start))
        movements.sort(key=lambda m: m["_ts"])
        for m in movements:
            del m["_ts"]

        self._write_with_defects(PRODUCTION_ORDERS.name, tag, order_rows, "csv")
        self._write_with_defects(INVENTORY_MOVEMENTS.name, tag, movements, "jsonl")
        drift = self.cfg.schema_drift_batch == batch
        if drift:
            for row in shipment_rows:
                row["carrier_service_level"] = self.rng.choice(["standard", "express", "economy"])
        self._write_with_defects(SHIPMENTS.name, tag, shipment_rows, "json")

    def _order_rows(
        self, cutoff: datetime, movements: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows = []
        variants = {"in_progress": "In Progress", "completed": "COMPLETE", "planned": "Planned"}
        for order in self.orders:
            if order.planned_start - timedelta(days=1) >= cutoff:
                continue
            status = order.status_at(cutoff)
            if status == order.last_status:
                continue
            order.last_status = status
            updated = {
                "planned": order.planned_start - timedelta(days=1),
                "cancelled": order.planned_start - timedelta(hours=20),
                "in_progress": order.actual_start + timedelta(minutes=10),
                "completed": order.actual_end + timedelta(minutes=10),
            }[status]
            started = status in ("in_progress", "completed")
            done = status == "completed"
            shown_status = (
                variants[status] if status in variants and self.rng.random() < 0.05 else status
            )
            rows.append(
                {
                    "production_order_id": order.order_id,
                    "product_id": order.product_id,
                    "warehouse_id": order.warehouse_id,
                    "status": shown_status,
                    "planned_quantity": order.planned_quantity,
                    "produced_quantity": order.produced if done else "",
                    "scrap_quantity": order.scrap if done else 0,
                    "planned_start": _local(order.planned_start),
                    "planned_end": _local(order.planned_end),
                    "actual_start": _local(order.actual_start) if started else "",
                    "actual_end": _local(order.actual_end) if done else "",
                    "updated_at": _local(updated),
                }
            )
            if done:
                movements.append(
                    self._movement(
                        order.actual_end,
                        order.product_id,
                        order.warehouse_id,
                        "production_output",
                        order.produced,
                        order.order_id,
                    )
                )
        return rows

    def _shipment_rows(
        self, cutoff: datetime, movements: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows = []
        for s in self.shipments:
            if s.created_at >= cutoff:
                continue
            status = s.status_at(cutoff)
            if status == s.last_status:
                continue
            s.last_status = status
            updated = {
                "ordered": s.created_at,
                "cancelled": s.created_at + timedelta(hours=3),
                "in_transit": s.shipped_at + timedelta(minutes=5),
                "delivered": s.delivered_at + timedelta(minutes=30),
            }[status]
            shipped = status in ("in_transit", "delivered")
            delivered = status == "delivered"
            promised = (
                s.promised_date.strftime("%d/%m/%Y")
                if self.rng.random() < 0.1
                else s.promised_date.isoformat()
            )
            rows.append(
                {
                    "shipment_id": s.shipment_id,
                    "supplier_id": s.supplier_id,
                    "product_id": s.product_id,
                    "warehouse_id": s.warehouse_id,
                    "status": status,
                    "carrier": s.carrier,
                    "quantity_ordered": s.quantity_ordered,
                    "quantity_received": s.received if delivered else None,
                    "quantity_rejected": s.rejected if delivered else 0,
                    "order_date": s.order_date.isoformat(),
                    "promised_date": promised,
                    "shipped_at": _iso_z(s.shipped_at) if shipped else None,
                    "delivered_at": _iso_z(s.delivered_at) if delivered else None,
                    "updated_at": _iso_z(updated),
                }
            )
            if delivered and s.received - s.rejected > 0:
                movements.append(
                    self._movement(
                        s.delivered_at,
                        s.product_id,
                        s.warehouse_id,
                        "receipt",
                        s.received - s.rejected,
                        s.shipment_id,
                    )
                )
        return rows

    def _daily_operations(self, start: datetime) -> list[dict[str, Any]]:
        """Customer issues, inter-warehouse transfers and cycle-count adjustments."""
        events: list[dict[str, Any]] = []
        warehouse_ids = [w[0] for w in _WAREHOUSES]
        for offset in range(self.cfg.days_per_batch):
            day = start + timedelta(days=offset)
            for wid in warehouse_ids:
                for product in self.rng.sample(self.products, 8):
                    available = self.on_hand[(product.product_id, wid)]
                    demand = self.rng.randint(5, max(6, product.reorder_point // 2))
                    qty = min(demand, available)
                    if qty > 0:
                        ts = day + timedelta(
                            hours=self.rng.randint(10, 19), minutes=self.rng.randint(0, 59)
                        )
                        events.append(
                            self._movement(ts, product.product_id, wid, "issue", qty, None)
                        )
            for _ in range(2):
                product = self.rng.choice(self.products)
                src, dst = self.rng.sample(warehouse_ids, 2)
                available = self.on_hand[(product.product_id, src)]
                if available < 50:
                    continue
                qty = self.rng.randint(10, available // 3)
                ref = f"TRF-{day:%Y%m%d}-{self.rng.randint(1000, 9999)}"
                ts = day + timedelta(hours=self.rng.randint(8, 16))
                events.append(self._movement(ts, product.product_id, src, "transfer_out", qty, ref))
                events.append(
                    self._movement(
                        ts + timedelta(hours=6), product.product_id, dst, "transfer_in", qty, ref
                    )
                )
            product = self.rng.choice(self.products)
            wid = self.rng.choice(warehouse_ids)
            adj = self.rng.choice([-5, -3, -2, -1, 1, 2, 4])
            if self.on_hand[(product.product_id, wid)] + adj >= 0:
                ts = day + timedelta(hours=21)
                events.append(
                    self._movement(ts, product.product_id, wid, "adjustment", adj, "CYCLE-COUNT")
                )
        return events

    # -------------------------------------------------------------- defects
    def _write_with_defects(
        self, entity: str, tag: str, rows: list[dict[str, Any]], fmt: str
    ) -> None:
        file_manifest = FileManifest(file=f"{entity}__{tag}.{fmt}", entity=entity, rows_written=0)
        rows = [dict(r) for r in rows]
        indices = list(range(len(rows)))
        self.rng.shuffle(indices)
        n_defects = round(len(rows) * self.cfg.defect_rate)
        n_dupes = round(len(rows) * self.cfg.duplicate_rate)
        defect_idx = indices[:n_defects]
        exact_idx = indices[n_defects : n_defects + n_dupes]
        conflict_idx = indices[n_defects + n_dupes : n_defects + 2 * n_dupes]
        injected: Counter[str] = Counter()

        appended: list[dict[str, Any]] = []
        for i in exact_idx:
            appended.append(dict(rows[i]))
        file_manifest.exact_duplicates = len(exact_idx)
        for i in conflict_idx:
            copy = dict(rows[i])
            _bump_quantity(entity, copy)
            appended.append(copy)
            injected[Reason.DUPLICATE_CONFLICT.value] += 1

        defects = _DEFECTS[entity]
        for n, i in enumerate(defect_idx):
            for attempt in range(len(defects)):
                apply = defects[(n + attempt) % len(defects)]
                reason = apply(rows[i])
                if reason is not None:
                    injected[reason.value] += 1
                    break

        rows.extend(appended)
        file_manifest.rows_written = len(rows)
        file_manifest.injected = dict(injected)
        if fmt == "csv":
            self._write_csv(
                entity, tag, list(rows[0].keys()) if rows else [], rows, manifest=file_manifest
            )
        elif fmt == "jsonl":
            path = self.out / file_manifest.file
            with path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    line = json.dumps({k: v for k, v in row.items() if k != "__truncate__"})
                    if row.get("__truncate__"):
                        line = line[: len(line) // 2]
                    handle.write(line + "\n")
            self.manifest.files.append(file_manifest)
        else:
            path = self.out / file_manifest.file
            path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
            self.manifest.files.append(file_manifest)

    def _write_csv(
        self,
        entity: str,
        tag: str,
        columns: tuple[str, ...] | list[str],
        rows: list[dict[str, Any]],
        manifest: FileManifest | None = None,
    ) -> None:
        name = f"{entity}__{tag}.csv"
        with (self.out / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
        self.manifest.files.append(
            manifest or FileManifest(file=name, entity=entity, rows_written=len(rows))
        )


def _local(ts: datetime) -> str:
    """Naive local time, as an MES on the factory floor would export it."""
    return ts.astimezone(LONDON).strftime("%Y-%m-%d %H:%M:%S")


def _iso_z(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bump_quantity(entity: str, row: dict[str, Any]) -> None:
    if entity == PRODUCTION_ORDERS.name:
        row["planned_quantity"] = int(row["planned_quantity"]) + 10
    elif entity == SHIPMENTS.name:
        row["quantity_ordered"] = int(row["quantity_ordered"]) + 10
    else:
        q = int(row["quantity"])
        row["quantity"] = q + 1 if q > 0 else q - 1


# Each defect mutates a row in place and returns the reason the pipeline is
# expected to report, or None if the defect does not apply to this row.
def _po_missing_product(row: dict[str, Any]) -> Reason | None:
    row["product_id"] = ""
    return Reason.MISSING_REQUIRED


def _po_impossible_date(row: dict[str, Any]) -> Reason | None:
    row["planned_start"] = "2026-02-30 08:00:00"
    return Reason.INVALID_DATE


def _po_negative_quantity(row: dict[str, Any]) -> Reason | None:
    row["planned_quantity"] = f"-{row['planned_quantity']}"
    return Reason.INVALID_QUANTITY


def _po_text_quantity(row: dict[str, Any]) -> Reason | None:
    row["planned_quantity"] = "fifty"
    return Reason.INVALID_QUANTITY


def _po_orphan_product(row: dict[str, Any]) -> Reason | None:
    row["product_id"] = "PRD-9999"
    return Reason.ORPHAN_REFERENCE


def _po_end_before_start(row: dict[str, Any]) -> Reason | None:
    if not row.get("actual_end"):
        return None
    start = datetime.fromisoformat(row["actual_start"])
    row["actual_end"] = (start - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    return Reason.INVALID_DATE


def _mv_missing_warehouse(row: dict[str, Any]) -> Reason | None:
    row["warehouse_id"] = None
    return Reason.MISSING_REQUIRED


def _mv_bad_timestamp(row: dict[str, Any]) -> Reason | None:
    row["event_timestamp"] = "not-a-timestamp"
    return Reason.INVALID_DATE


def _mv_negative_issue(row: dict[str, Any]) -> Reason | None:
    if row["movement_type"] == "adjustment":
        return None
    row["quantity"] = -abs(int(row["quantity"]))
    return Reason.INVALID_QUANTITY


def _mv_orphan_product(row: dict[str, Any]) -> Reason | None:
    row["product_id"] = "PRD-9999"
    return Reason.ORPHAN_REFERENCE


def _mv_unknown_type(row: dict[str, Any]) -> Reason | None:
    row["movement_type"] = "teleport"
    return Reason.INVALID_VALUE


def _mv_truncated_line(row: dict[str, Any]) -> Reason | None:
    row["__truncate__"] = True
    return Reason.MALFORMED_RECORD


def _sh_missing_supplier(row: dict[str, Any]) -> Reason | None:
    row["supplier_id"] = None
    return Reason.MISSING_REQUIRED


def _sh_impossible_date(row: dict[str, Any]) -> Reason | None:
    row["promised_date"] = "31/13/2026"
    return Reason.INVALID_DATE


def _sh_delivered_before_shipped(row: dict[str, Any]) -> Reason | None:
    if not row.get("delivered_at") or not row.get("shipped_at"):
        return None
    shipped = datetime.fromisoformat(row["shipped_at"])
    row["delivered_at"] = _iso_z(shipped - timedelta(days=1))
    return Reason.INVALID_DATE


def _sh_negative_quantity(row: dict[str, Any]) -> Reason | None:
    row["quantity_ordered"] = -10
    return Reason.INVALID_QUANTITY


def _sh_orphan_warehouse(row: dict[str, Any]) -> Reason | None:
    row["warehouse_id"] = "WH-XXX"
    return Reason.ORPHAN_REFERENCE


_DEFECTS = {
    PRODUCTION_ORDERS.name: [
        _po_missing_product,
        _po_impossible_date,
        _po_negative_quantity,
        _po_text_quantity,
        _po_orphan_product,
        _po_end_before_start,
    ],
    INVENTORY_MOVEMENTS.name: [
        _mv_missing_warehouse,
        _mv_bad_timestamp,
        _mv_negative_issue,
        _mv_orphan_product,
        _mv_unknown_type,
        _mv_truncated_line,
    ],
    SHIPMENTS.name: [
        _sh_missing_supplier,
        _sh_impossible_date,
        _sh_delivered_before_shipped,
        _sh_negative_quantity,
        _sh_orphan_warehouse,
    ],
}


def generate(output_dir: Path, config: GeneratorConfig | None = None) -> GenerationManifest:
    """Write synthetic landing files to ``output_dir`` and return the manifest."""
    return _Generator(config or GeneratorConfig(), output_dir).run()
