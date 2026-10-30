# Manufacturing and supply-chain KPI report

Data as of **2026-04-04**.

## Operational summary

Headline KPIs across production, procurement, inventory and data quality.

| KPI | Value |
|---|---|
| as_of_date | 2026-04-04 |
| units_produced | 109,397 |
| plan_attainment_pct | 95.39 |
| scrap_rate_pct | 2.08 |
| on_time_completion_pct | 38.79 |
| shipments_delivered | 600 |
| supplier_otif_pct | 70.67 |
| avg_supplier_lead_time_days | 8.85 |
| inventory_value | 48,688,778.33 |
| stockout_positions | 18 |
| below_reorder_positions | 35 |
| quarantine_rate_pct | 2.46 |

## Production performance by month and category

Completed orders only. Plan attainment = produced / planned units; scrap rate = scrap / (produced + scrap); on time = finished no later than the planned end.

| month | category | orders_completed | produced_units | plan_attainment_pct | scrap_rate_pct | on_time_completion_pct | avg_cycle_hours |
|---|---|---|---|---|---|---|---|
| 2026-01 | Electrical Assemblies | 98 | 10,685 | 94.73 | 2.27 | 34.69 | 20.90 |
| 2026-01 | Hydraulic Components | 98 | 10,347 | 95.10 | 2 | 40.82 | 21.30 |
| 2026-01 | Sensors | 113 | 11,966 | 95.73 | 2.33 | 37.17 | 20.10 |
| 2026-02 | Electrical Assemblies | 111 | 12,151 | 95.68 | 2.24 | 36.94 | 21 |
| 2026-02 | Hydraulic Components | 109 | 11,412 | 95.74 | 2.11 | 40.37 | 20.50 |
| 2026-02 | Sensors | 94 | 8,995 | 95.49 | 1.97 | 43.62 | 17.90 |
| 2026-03 | Electrical Assemblies | 122 | 12,170 | 95.08 | 2.06 | 40.98 | 21.70 |
| 2026-03 | Hydraulic Components | 115 | 11,407 | 95.62 | 1.77 | 35.65 | 21.60 |
| 2026-03 | Sensors | 137 | 14,957 | 95.45 | 1.95 | 37.96 | 20.90 |
| 2026-04 | Electrical Assemblies | 21 | 2,312 | 97.55 | 2.12 | 28.57 | 17.60 |
| 2026-04 | Hydraulic Components | 17 | 1,693 | 92.51 | 2.70 | 35.29 | 23.20 |
| 2026-04 | Sensors | 9 | 1,302 | 94.35 | 1.21 | 88.89 | 21.10 |

## Supplier scorecard

OTIF = delivered on or before the promised date AND accepted quantity >= 98% of ordered. Sorted from worst to best OTIF.

| supplier_id | supplier_name | country_code | contracted_lead_time_days | deliveries | on_time_pct | in_full_pct | otif_pct | avg_lead_time_days | avg_days_late_when_late | defect_rate_pct | open_shipments | open_overdue |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SUP-005 | Severn Hydraulic Systems | GB | 9 | 29 | 79.31 | 41.38 | 34.48 | 8.93 | 4.17 | 2.52 | 2 | 0 |
| SUP-011 | Kestrel Industrial Supply | IE | 9 | 83 | 79.52 | 43.37 | 34.94 | 8.98 | 3.35 | 2.29 | 9 | 1 |
| SUP-008 | Iberia Alloys | ES | 18 | 40 | 75 | 67.50 | 50 | 17.55 | 4.10 | 1.85 | 14 | 0 |
| SUP-003 | Brightwater Electronics | GB | 10 | 42 | 78.57 | 64.29 | 52.38 | 10.33 | 5.44 | 2.01 | 5 | 0 |
| SUP-009 | Vistula Cable Works | PL | 12 | 19 | 84.21 | 84.21 | 68.42 | 11.89 | 4.67 | 1.21 | 4 | 0 |
| SUP-006 | Tynebridge Sensor Works | GB | 8 | 62 | 90.32 | 87.10 | 77.42 | 7.58 | 5.33 | 1.55 | 3 | 0 |
| SUP-004 | Pennine Polymers | GB | 6 | 32 | 87.50 | 96.88 | 84.38 | 5.78 | 5 | 1.27 | 1 | 0 |
| SUP-012 | Brabant Measurement Systems | NL | 11 | 47 | 95.74 | 89.36 | 85.11 | 10.21 | 6 | 1.06 | 6 | 0 |
| SUP-001 | Northgate Precision Castings | GB | 7 | 55 | 92.73 | 92.73 | 85.45 | 6.55 | 5 | 1.01 | 8 | 0 |
| SUP-010 | Ashby Metal Pressings | GB | 6 | 81 | 95.06 | 91.36 | 86.42 | 5.30 | 3.50 | 0.84 | 5 | 1 |
| SUP-007 | Lindenhof Komponenten | DE | 14 | 62 | 93.55 | 93.55 | 87.10 | 13.24 | 3.25 | 0.60 | 12 | 0 |
| SUP-002 | Calder Valley Fasteners | GB | 5 | 48 | 97.92 | 93.75 | 91.67 | 4.19 | 4 | 0.42 | 1 | 0 |

## Shipment delay distribution

Delivered shipments by days late against the promised date.

| delay_bucket | shipments | share_pct |
|---|---|---|
| On time or early | 530 | 88.33 |
| 1-2 days late | 12 | 2 |
| 3-5 days late | 35 | 5.83 |
| 6+ days late | 23 | 3.83 |

## Inventory below reorder point

Stock positions below their reorder point, lowest cover first. Days of cover uses the average daily issue over the last 30 days.

| product_id | product_name | warehouse_id | on_hand | reorder_point | avg_daily_issue_30d | days_of_cover | is_stockout |
|---|---|---|---|---|---|---|---|
| PRD-0020 | Lock Nut M10 LNM-200 | WH-BHM | -231 | 120 | 11.53 | 0 | yes |
| PRD-0024 | Anchor Bolt AB-200 | WH-MAN | -207 | 200 | 18.03 | 0 | yes |
| PRD-0036 | Aluminium Bar AB-200 | WH-MAN | -55 | 50 | 2.83 | 0 | yes |
| PRD-0007 | Hose Assembly HA-100 | WH-GLA | -49 | 300 | 15.53 | 0 | yes |
| PRD-0003 | Pressure Relief Valve PRV-100 | WH-BRS | 0 | 300 | 3.30 | 0 | yes |
| PRD-0016 | Power Supply Unit PSU-200 | WH-BRS | 0 | 300 | 1.67 | 0 | yes |
| PRD-0018 | Hex Bolt M12 HBM-200 | WH-LDS | 0 | 120 | 3.07 | 0 | yes |
| PRD-0023 | Anchor Bolt AB-100 | WH-LDS | 0 | 300 | 10.50 | 0 | yes |
| PRD-0024 | Anchor Bolt AB-200 | WH-BRS | 0 | 200 | 5.80 | 0 | yes |
| PRD-0032 | Pressure Transducer PT-200 | WH-BRS | 0 | 300 | 16.50 | 0 | yes |
| PRD-0033 | Steel Sheet 2mm SS2-100 | WH-BRS | 0 | 300 | 8.50 | 0 | yes |
| PRD-0033 | Steel Sheet 2mm SS2-100 | WH-MAN | 0 | 300 | 0.73 | 0 | yes |
| PRD-0035 | Aluminium Bar AB-100 | WH-GLA | 0 | 80 | 0.07 | 0 | yes |
| PRD-0038 | Copper Wire CW-200 | WH-GLA | 0 | 50 | 3.97 | 0 | yes |
| PRD-0040 | Polymer Granulate PG-200 | WH-GLA | 0 | 300 | 9.87 | 0 | yes |

_Showing 15 of 35 rows; see `inventory_risk.csv` for all._

## Data quality: quarantined records

Records rejected during ingestion, by entity and reason. A record can carry more than one reason.

| entity | reason | records |
|---|---|---|
| inventory_movements | duplicate_conflict | 28 |
| inventory_movements | missing_required | 20 |
| inventory_movements | invalid_date | 19 |
| inventory_movements | invalid_quantity | 19 |
| inventory_movements | orphan_reference | 19 |
| inventory_movements | invalid_value | 18 |
| inventory_movements | malformed_record | 18 |
| production_orders | invalid_date | 7 |
| production_orders | duplicate_conflict | 6 |
| production_orders | invalid_quantity | 6 |
| production_orders | missing_required | 6 |
| production_orders | orphan_reference | 3 |
| shipments | duplicate_conflict | 5 |
| shipments | invalid_quantity | 5 |
| shipments | missing_required | 5 |
| shipments | invalid_date | 4 |
| shipments | orphan_reference | 3 |
