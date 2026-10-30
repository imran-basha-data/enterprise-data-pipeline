-- Movements are immutable events, so new rows are inserted and never updated.
INSERT INTO mart.fact_inventory_movement (
    movement_id, product_key, warehouse_key, date_key, movement_type, quantity_delta,
    reference_id, event_timestamp
)
SELECT
    m.movement_id,
    p.product_key,
    w.warehouse_key,
    to_char(m.event_timestamp AT TIME ZONE %(tz)s, 'YYYYMMDD')::integer,
    m.movement_type,
    m.quantity_delta,
    m.reference_id,
    m.event_timestamp
FROM staging.inventory_movements AS m
JOIN mart.dim_product AS p ON p.product_id = m.product_id
JOIN mart.dim_warehouse AS w ON w.warehouse_id = m.warehouse_id
WHERE m._change_seq > %(low)s AND m._change_seq <= %(high)s
ON CONFLICT (movement_id) DO NOTHING;
