-- KPI views over the star schema. Each view states its metric definitions
-- once, so reports, dashboards and ad-hoc queries all agree.

-- The latest point in time covered by the data. Open-item metrics such as
-- "overdue" are measured against this rather than the wall clock, so results
-- are reproducible for historical or synthetic data.
CREATE VIEW mart.v_data_as_of AS
SELECT
    greatest(
        (SELECT max(event_timestamp) FROM mart.fact_inventory_movement),
        (SELECT max(source_updated_at) FROM mart.fact_production_order),
        (SELECT max(source_updated_at) FROM mart.fact_shipment)
    ) AS as_of_ts,
    (greatest(
        (SELECT max(event_timestamp) FROM mart.fact_inventory_movement),
        (SELECT max(source_updated_at) FROM mart.fact_production_order),
        (SELECT max(source_updated_at) FROM mart.fact_shipment)
    ) AT TIME ZONE 'UTC')::date AS as_of_date;

-- Production performance by completion week and product category.
CREATE VIEW mart.v_production_performance AS
SELECT
    d.week_start,
    p.category,
    count(*)                                                    AS orders_completed,
    sum(f.planned_quantity)                                     AS planned_units,
    sum(f.produced_quantity)                                    AS produced_units,
    sum(f.scrap_quantity)                                       AS scrap_units,
    round(100.0 * sum(f.produced_quantity)
          / nullif(sum(f.planned_quantity), 0), 2)              AS plan_attainment_pct,
    round(100.0 * sum(f.scrap_quantity)
          / nullif(sum(f.produced_quantity + f.scrap_quantity), 0), 2) AS scrap_rate_pct,
    round(100.0 * avg(f.is_on_time::integer), 2)                AS on_time_completion_pct,
    round(avg(f.actual_hours), 2)                               AS avg_cycle_hours
FROM mart.fact_production_order AS f
JOIN mart.dim_date AS d ON d.date_key = f.completed_date_key
JOIN mart.dim_product AS p ON p.product_key = f.product_key
WHERE f.is_completed
GROUP BY d.week_start, p.category;

-- Current stock position per product and warehouse.
CREATE VIEW mart.v_inventory_position AS
WITH as_of AS (SELECT as_of_date FROM mart.v_data_as_of),
positions AS (
    SELECT
        m.product_key,
        m.warehouse_key,
        sum(m.quantity_delta) AS on_hand,
        coalesce(sum(-m.quantity_delta) FILTER (
            WHERE m.movement_type = 'issue'
              AND m.event_timestamp >= (SELECT as_of_date FROM as_of) - 30
        ), 0) AS issued_last_30d
    FROM mart.fact_inventory_movement AS m
    GROUP BY m.product_key, m.warehouse_key
)
SELECT
    p.product_id,
    p.product_name,
    p.category,
    w.warehouse_id,
    w.warehouse_name,
    pos.on_hand,
    p.reorder_point,
    round(greatest(pos.on_hand, 0) * p.unit_cost, 2)            AS stock_value,
    round(pos.issued_last_30d / 30.0, 2)                        AS avg_daily_issue_30d,
    CASE WHEN pos.issued_last_30d > 0
         THEN round(greatest(pos.on_hand, 0) / (pos.issued_last_30d / 30.0), 1)
    END                                                         AS days_of_cover,
    pos.on_hand <= 0                                            AS is_stockout,
    pos.on_hand < p.reorder_point                               AS is_below_reorder_point
FROM positions AS pos
JOIN mart.dim_product AS p ON p.product_key = pos.product_key
JOIN mart.dim_warehouse AS w ON w.warehouse_key = pos.warehouse_key;

-- Supplier delivery performance (OTIF), lead time and quality.
CREATE VIEW mart.v_supplier_scorecard AS
SELECT
    s.supplier_id,
    s.supplier_name,
    s.country_code,
    s.contracted_lead_time_days,
    count(*) FILTER (WHERE f.status = 'delivered')              AS deliveries,
    round(100.0 * avg(f.is_on_time::integer), 2)                AS on_time_pct,
    round(100.0 * avg(f.is_in_full::integer), 2)                AS in_full_pct,
    round(100.0 * avg((f.is_on_time AND f.is_in_full)::integer), 2) AS otif_pct,
    round(avg(f.lead_time_days), 2)                             AS avg_lead_time_days,
    round(avg(f.delay_days) FILTER (WHERE f.delay_days > 0), 2) AS avg_days_late_when_late,
    round(100.0 * sum(f.quantity_rejected)
          / nullif(sum(f.quantity_received), 0), 2)             AS defect_rate_pct,
    count(*) FILTER (WHERE f.status IN ('ordered', 'in_transit')) AS open_shipments,
    count(*) FILTER (
        WHERE f.status IN ('ordered', 'in_transit') AND f.promised_date < a.as_of_date
    )                                                           AS open_overdue
FROM mart.fact_shipment AS f
JOIN mart.dim_supplier AS s ON s.supplier_key = f.supplier_key
CROSS JOIN mart.v_data_as_of AS a
GROUP BY s.supplier_id, s.supplier_name, s.country_code, s.contracted_lead_time_days;

-- Distribution of delivery delays against the promised date.
CREATE VIEW mart.v_shipment_delays AS
WITH buckets AS (
    SELECT
        CASE
            WHEN delay_days <= 0 THEN 1
            WHEN delay_days <= 2 THEN 2
            WHEN delay_days <= 5 THEN 3
            ELSE 4
        END AS bucket_order
    FROM mart.fact_shipment
    WHERE status = 'delivered'
)
SELECT
    bucket_order,
    (ARRAY['On time or early', '1-2 days late', '3-5 days late', '6+ days late'])[bucket_order]
                                                                AS delay_bucket,
    count(*)                                                    AS shipments,
    round(100.0 * count(*) / sum(count(*)) OVER (), 2)          AS share_pct
FROM buckets
GROUP BY bucket_order;

-- One-row operational summary.
CREATE VIEW mart.v_operational_kpis AS
SELECT
    a.as_of_date,
    (SELECT sum(produced_quantity) FROM mart.fact_production_order WHERE is_completed)
                                                                AS units_produced,
    (SELECT round(100.0 * sum(produced_quantity) / nullif(sum(planned_quantity), 0), 2)
       FROM mart.fact_production_order WHERE is_completed)     AS plan_attainment_pct,
    (SELECT round(100.0 * sum(scrap_quantity)
                  / nullif(sum(produced_quantity + scrap_quantity), 0), 2)
       FROM mart.fact_production_order WHERE is_completed)     AS scrap_rate_pct,
    (SELECT round(100.0 * avg(is_on_time::integer), 2)
       FROM mart.fact_production_order WHERE is_completed)     AS on_time_completion_pct,
    (SELECT count(*) FROM mart.fact_shipment WHERE status = 'delivered')
                                                                AS shipments_delivered,
    (SELECT round(100.0 * avg((is_on_time AND is_in_full)::integer), 2)
       FROM mart.fact_shipment WHERE status = 'delivered')     AS supplier_otif_pct,
    (SELECT round(avg(lead_time_days), 2)
       FROM mart.fact_shipment WHERE status = 'delivered')     AS avg_supplier_lead_time_days,
    (SELECT sum(stock_value) FROM mart.v_inventory_position)   AS inventory_value,
    (SELECT count(*) FROM mart.v_inventory_position WHERE is_stockout)
                                                                AS stockout_positions,
    (SELECT count(*) FROM mart.v_inventory_position WHERE is_below_reorder_point)
                                                                AS below_reorder_positions,
    (SELECT round(100.0 * sum(rows_quarantined) / nullif(sum(rows_read), 0), 2)
       FROM ops.ingested_files)                                AS quarantine_rate_pct
FROM mart.v_data_as_of AS a;
