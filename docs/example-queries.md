# Example queries

Every result below is real output from `edp generate --seed 42 && edp run` on a clean PostgreSQL 16 database.

## What happened to each file?

```sql
SELECT file_name, status, rows_read, rows_inserted, rows_updated, rows_unchanged,
       rows_duplicate, rows_quarantined
FROM ops.ingested_files ORDER BY file_id;
```

```text
              file_name               | status | rows_read | rows_inserted | rows_updated | rows_unchanged | rows_duplicate | rows_quarantined
--------------------------------------+--------+-----------+---------------+--------------+----------------+----------------+------------------
 suppliers__batch_001.csv             | loaded |        12 |            12 |            0 |              0 |              0 |                0
 suppliers__batch_002.csv             | loaded |         1 |             0 |            1 |              0 |              0 |                0
 warehouses__batch_001.csv            | loaded |         5 |             5 |            0 |              0 |              0 |                0
 products__batch_001.csv              | loaded |        40 |            40 |            0 |              0 |              0 |                0
 products__batch_002.csv              | loaded |         6 |             0 |            4 |              2 |              0 |                0
 products__batch_003.csv              | loaded |         6 |             0 |            4 |              2 |              0 |                0
 inventory_movements__batch_001.jsonl | loaded |      2027 |          1967 |            0 |              0 |             10 |               50
 inventory_movements__batch_002.jsonl | loaded |      1825 |          1771 |            0 |              0 |              9 |               45
 inventory_movements__batch_003.jsonl | loaded |      1852 |          1797 |            0 |              0 |              9 |               46
 production_orders__batch_001.csv     | loaded |       374 |           363 |            0 |              0 |              2 |                9
 production_orders__batch_002.csv     | loaded |       372 |           354 |            7 |              0 |              2 |                9
 production_orders__batch_003.csv     | loaded |       395 |           369 |           14 |              0 |              2 |               10
 shipments__batch_001.json            | loaded |       232 |           225 |            0 |              0 |              1 |                6
 shipments__batch_002.json            | loaded |       312 |           231 |           71 |              0 |              2 |                8
 shipments__batch_003.json            | loaded |       311 |           227 |           74 |              0 |              2 |                8
```

Batches 2 and 3 update earlier records: shipments delivered after being in transit, and orders completed. Re-sent product rows with identical content are counted as unchanged.

## Why were records rejected?

```sql
SELECT entity, row_number, record_key, issues->0->>'detail' AS detail
FROM ops.quarantine WHERE entity = 'shipments' ORDER BY quarantine_id LIMIT 4;
```

```text
  entity   | row_number |   record_key    |                     detail
-----------+------------+-----------------+-------------------------------------------------
 shipments |         16 | SHP-2026-000016 | promised_date: '31/13/2026' is not a valid date
 shipments |         99 | SHP-2026-000099 | quantity_ordered -10 is below 1
 shipments |        127 | SHP-2026-000127 | warehouse_id 'WH-XXX' not found in warehouses
 shipments |        165 | SHP-2026-000165 | supplier_id is required
```

The original payload is kept in `ops.quarantine.payload`, so a corrected record can be traced back to the exact source row.

## Plan attainment and on-time completion by category (February)

```sql
SELECT p.category, d.month_name,
       round(100.0 * sum(f.produced_quantity) / sum(f.planned_quantity), 2) AS plan_attainment_pct,
       round(100.0 * avg(f.is_on_time::int), 1) AS on_time_pct
FROM mart.fact_production_order f
JOIN mart.dim_product p USING (product_key)
JOIN mart.dim_date d ON d.date_key = f.completed_date_key
WHERE f.is_completed AND d.month = 2
GROUP BY 1, 2 ORDER BY 1;
```

```text
       category        | month_name | plan_attainment_pct | on_time_pct
-----------------------+------------+---------------------+-------------
 Electrical Assemblies | February   |               95.68 |        36.9
 Hydraulic Components  | February   |               95.74 |        40.4
 Sensors               | February   |               95.49 |        43.6
```

## Worst suppliers by OTIF

```sql
SELECT supplier_id, supplier_name, deliveries, otif_pct, avg_lead_time_days, defect_rate_pct
FROM mart.v_supplier_scorecard ORDER BY otif_pct LIMIT 3;
```

```text
 supplier_id |       supplier_name       | deliveries | otif_pct | avg_lead_time_days | defect_rate_pct
-------------+---------------------------+------------+----------+--------------------+-----------------
 SUP-005     | Severn Hydraulic Systems  |         29 |    34.48 |               8.93 |            2.52
 SUP-011     | Kestrel Industrial Supply |         83 |    34.94 |               8.98 |            2.29
 SUP-008     | Iberia Alloys             |         40 |    50.00 |              17.55 |            1.85
```

## Most delayed deliveries

```sql
SELECT s.supplier_name, f.shipment_id, f.promised_date, f.delivered_date, f.delay_days
FROM mart.fact_shipment f JOIN mart.dim_supplier s USING (supplier_key)
WHERE f.delay_days > 0
ORDER BY f.delay_days DESC, f.shipment_id LIMIT 5;
```

```text
      supplier_name      |   shipment_id   | promised_date | delivered_date | delay_days
-------------------------+-----------------+---------------+----------------+------------
 Ashby Metal Pressings   | SHP-2026-000113 | 2026-01-25    | 2026-02-01     |          7
 Brightwater Electronics | SHP-2026-000120 | 2026-01-31    | 2026-02-07     |          7
 Tynebridge Sensor Works | SHP-2026-000168 | 2026-02-04    | 2026-02-11     |          7
 Brightwater Electronics | SHP-2026-000366 | 2026-03-03    | 2026-03-10     |          7
 Brightwater Electronics | SHP-2026-000482 | 2026-03-18    | 2026-03-25     |          7
```

## Incremental watermarks

```sql
SELECT model_name, last_change_seq, rows_affected FROM ops.model_watermarks ORDER BY model_name;
```

```text
       model_name        | last_change_seq | rows_affected
-------------------------+-----------------+---------------
 dim_product             |              75 |            40
 dim_supplier            |              14 |            12
 dim_warehouse           |              19 |             5
 fact_inventory_movement |            5610 |          5535
 fact_production_order   |            6738 |          1086
 fact_shipment           |            7711 |           683
```

## Dataset quality gate results

```sql
SELECT check_name, severity, passed, observed_value
FROM ops.dq_results WHERE file_id IS NULL ORDER BY result_id;
```

```text
          check_name           | severity | passed | observed_value
-------------------------------+----------+--------+----------------
 reconcile_suppliers           | error    | t      |              0
 reconcile_warehouses          | error    | t      |              0
 reconcile_products            | error    | t      |              0
 reconcile_production_orders   | error    | t      |              0
 reconcile_inventory_movements | error    | t      |              0
 reconcile_shipments           | error    | t      |              0
 reconcile_stock_units         | error    | t      |              0
 negative_stock_positions      | warning  | f      |              4
 negative_lead_times           | error    | t      |              0
 run_quarantine_rate           | warning  | t      |         0.0246
```

The four negative stock positions are a real downstream effect of quarantined data. Running the same seed with defect injection disabled gives zero negative positions and zero quarantined rows. In two of the four cases a quarantined movement for the same product and warehouse is directly visible (a `receipt` of −49, and a 992-unit movement whose `movement_type` was corrupted to `teleport`). The warning is how an analyst finds out that rejected records affect stock figures.
