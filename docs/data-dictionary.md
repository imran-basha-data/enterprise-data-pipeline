# Data dictionary

Source contracts are defined in [`src/edp/contracts.py`](../src/edp/contracts.py). The tables below describe each field as the pipeline accepts it. **R** = required.

## Cleaning rules applied to every field

- Leading and trailing whitespace is removed. Empty values and the tokens `null`, `none`, `n/a`, `na`, `nan` and `-` become NULL.
- Identifiers are upper-cased (`wh-bhm` → `WH-BHM`). Categories and cities are title-cased.
- Status and type codes are lower snake case (`In Progress` → `in_progress`). Known synonyms are mapped (`COMPLETE`/`done` → `completed`, `canceled` → `cancelled`).
- Timestamps are stored in UTC. Naive values are read in `EDP_SOURCE_TIMEZONE` (default Europe/London, so BST is handled). Epoch seconds and milliseconds are accepted.
- Dates accept ISO `YYYY-MM-DD` and UK `DD/MM/YYYY`.
- Integers must be whole numbers within PostgreSQL's `integer` range. `1,200` and `12.0` are accepted.
- Timestamps before 2000-01-01 are invalid. So are timestamps more than one day in the future, except fields marked *future allowed*.
- Optional fields with a default (`is_active` → true, `scrap_quantity` → 0, ...) take it when empty.

## Reason codes

| Code | Meaning |
|---|---|
| `missing_required` | a required field is empty, or a status requires a field that is empty |
| `invalid_date` | unparseable or impossible date/time, out of bounds, or out of order (end before start) |
| `invalid_quantity` | non-numeric, negative or out-of-range quantity, or a quantity rule broken (over-production, over-receipt) |
| `invalid_value` | value outside an allowed set, wrong identifier format, or too long |
| `orphan_reference` | foreign key not found in staging |
| `duplicate_conflict` | same key and version as another record, with different content |
| `malformed_record` | row cannot be parsed (wrong field count, invalid JSON line) |

## suppliers (CSV)

| Field | Type | R | Rules |
|---|---|---|---|
| supplier_id | string | R | `SUP-` + 3+ digits |
| supplier_name | string | R | ≤ 200 chars |
| country_code | string | R | ISO-3166 alpha-2 |
| contracted_lead_time_days | integer | R | 0–365 |
| is_active | boolean | | default true |
| updated_at | timestamp | R | version column |

## warehouses (CSV)

| Field | Type | R | Rules |
|---|---|---|---|
| warehouse_id | string | R | `WH-` + 3 letters |
| warehouse_name | string | R | ≤ 200 chars |
| city | string | R | title case |
| region | string | | title case |
| country_code | string | R | alpha-2 |
| capacity_units | integer | | ≥ 0 |
| is_active | boolean | | default true |
| updated_at | timestamp | R | version column |

## products (CSV)

| Field | Type | R | Rules |
|---|---|---|---|
| product_id | string | R | `PRD-` + 4 digits |
| sku | string | R | ≤ 40 chars |
| product_name | string | R | ≤ 200 chars |
| category | string | R | title case |
| unit_of_measure | string | R | one of `EA`, `KG`, `M`, `L`, `BOX` |
| unit_cost | decimal | R | 0.01 – 9,999,999,999.99 |
| reorder_point | integer | | ≥ 0, default 0 |
| primary_supplier_id | string | R | must exist in suppliers |
| is_active | boolean | | default true |
| updated_at | timestamp | R | version column |

## production_orders (CSV, naive local timestamps)

| Field | Type | R | Rules |
|---|---|---|---|
| production_order_id | string | R | `MO-YYYY-NNNNNN` |
| product_id | string | R | must exist in products |
| warehouse_id | string | R | must exist in warehouses |
| status | string | R | `planned`, `in_progress`, `completed`, `cancelled` |
| planned_quantity | integer | R | ≥ 1 |
| produced_quantity | integer | | ≥ 0; required when completed; ≤ 150% of planned |
| scrap_quantity | integer | | ≥ 0, default 0 |
| planned_start | timestamp | R | future allowed |
| planned_end | timestamp | R | future allowed; ≥ planned_start |
| actual_start | timestamp | | required if actual_end is set |
| actual_end | timestamp | | ≥ actual_start; required when completed |
| updated_at | timestamp | R | version column |

## inventory_movements (JSON Lines, immutable events)

| Field | Type | R | Rules |
|---|---|---|---|
| movement_id | uuid | R | event identifier |
| product_id | string | R | must exist in products |
| warehouse_id | string | R | must exist in warehouses |
| movement_type | string | R | `receipt`, `issue`, `production_output`, `adjustment`, `transfer_in`, `transfer_out` |
| quantity | integer | R | non-zero; negative only for `adjustment` |
| reference_id | string | | ≤ 64 chars (shipment, order or transfer reference) |
| event_timestamp | timestamp | R | ISO, offset, or epoch ms |

Derived in staging: `quantity_delta` is the signed effect on stock. `issue` and `transfer_out` are negative, `adjustment` keeps its sign, everything else is positive.

## shipments (JSON array)

| Field | Type | R | Rules |
|---|---|---|---|
| shipment_id | string | R | `SHP-YYYY-NNNNNN` |
| supplier_id | string | R | must exist in suppliers |
| product_id | string | R | must exist in products |
| warehouse_id | string | R | must exist in warehouses |
| status | string | R | `ordered`, `in_transit`, `delivered`, `cancelled` |
| carrier | string | | ≤ 100 chars |
| quantity_ordered | integer | R | ≥ 1 |
| quantity_received | integer | | ≥ 0; required when delivered; ≤ 110% of ordered |
| quantity_rejected | integer | | ≥ 0, default 0; ≤ quantity_received |
| order_date | date | R | |
| promised_date | date | R | future allowed; ≥ order_date |
| shipped_at | timestamp | | date ≥ order_date; required if delivered_at is set |
| delivered_at | timestamp | | ≥ shipped_at; required when delivered |
| updated_at | timestamp | R | version column |

## KPI definitions

| KPI | Definition |
|---|---|
| Plan attainment % | Σ produced ÷ Σ planned, completed orders |
| Scrap rate % | Σ scrap ÷ Σ (produced + scrap) |
| On-time completion % | completed orders with `actual_end ≤ planned_end` |
| Supplier on time | delivered (local date) ≤ promised date |
| Supplier in full | (received − rejected) ≥ 98% of ordered |
| OTIF % | delivered shipments that are both on time and in full |
| Lead time (days) | delivered date − order date |
| Defect rate % | Σ rejected ÷ Σ received |
| On hand | Σ `quantity_delta` per product and warehouse |
| Days of cover | max(on hand, 0) ÷ average daily issues over the last 30 days |
| Open overdue | shipments `ordered`/`in_transit` with promised date before the data's as-of date |
| Quarantine rate % | Σ rows quarantined ÷ Σ rows read |
