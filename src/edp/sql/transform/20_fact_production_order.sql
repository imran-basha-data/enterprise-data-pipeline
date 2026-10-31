-- Derived measures are computed once here so every report uses one definition.
INSERT INTO mart.fact_production_order (
    production_order_id, product_key, warehouse_key, planned_start_date_key,
    completed_date_key, status, planned_quantity, produced_quantity, scrap_quantity,
    planned_hours, actual_hours, start_delay_minutes, completion_delay_minutes,
    is_completed, is_on_time, source_updated_at
)
SELECT
    po.production_order_id,
    p.product_key,
    w.warehouse_key,
    to_char(po.planned_start AT TIME ZONE %(tz)s, 'YYYYMMDD')::integer,
    CASE WHEN po.status = 'completed'
         THEN to_char(po.actual_end AT TIME ZONE %(tz)s, 'YYYYMMDD')::integer END,
    po.status,
    po.planned_quantity,
    po.produced_quantity,
    po.scrap_quantity,
    round(extract(epoch FROM po.planned_end - po.planned_start) / 3600, 2),
    round(extract(epoch FROM po.actual_end - po.actual_start) / 3600, 2),
    round(extract(epoch FROM po.actual_start - po.planned_start) / 60)::integer,
    round(extract(epoch FROM po.actual_end - po.planned_end) / 60)::integer,
    po.status = 'completed',
    CASE WHEN po.status = 'completed' THEN po.actual_end <= po.planned_end END,
    po.updated_at
FROM staging.production_orders AS po
JOIN mart.dim_product AS p ON p.product_id = po.product_id
JOIN mart.dim_warehouse AS w ON w.warehouse_id = po.warehouse_id
WHERE po._change_seq > %(low)s AND po._change_seq <= %(high)s
ON CONFLICT (production_order_id) DO UPDATE SET
    product_key = EXCLUDED.product_key,
    warehouse_key = EXCLUDED.warehouse_key,
    planned_start_date_key = EXCLUDED.planned_start_date_key,
    completed_date_key = EXCLUDED.completed_date_key,
    status = EXCLUDED.status,
    planned_quantity = EXCLUDED.planned_quantity,
    produced_quantity = EXCLUDED.produced_quantity,
    scrap_quantity = EXCLUDED.scrap_quantity,
    planned_hours = EXCLUDED.planned_hours,
    actual_hours = EXCLUDED.actual_hours,
    start_delay_minutes = EXCLUDED.start_delay_minutes,
    completion_delay_minutes = EXCLUDED.completion_delay_minutes,
    is_completed = EXCLUDED.is_completed,
    is_on_time = EXCLUDED.is_on_time,
    source_updated_at = EXCLUDED.source_updated_at;
