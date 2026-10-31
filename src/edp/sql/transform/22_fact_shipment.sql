-- On time: delivered on or before the promised date.
-- In full: accepted quantity (received - rejected) is at least 98 percent of ordered.
INSERT INTO mart.fact_shipment (
    shipment_id, supplier_key, product_key, warehouse_key, order_date_key, promised_date_key,
    delivered_date_key, status, carrier, quantity_ordered, quantity_received,
    quantity_rejected, quantity_accepted, order_date, promised_date, delivered_date,
    lead_time_days, delay_days, is_on_time, is_in_full, source_updated_at
)
SELECT
    s.shipment_id,
    sup.supplier_key,
    p.product_key,
    w.warehouse_key,
    to_char(s.order_date, 'YYYYMMDD')::integer,
    to_char(s.promised_date, 'YYYYMMDD')::integer,
    to_char(d.delivered_date, 'YYYYMMDD')::integer,
    s.status,
    s.carrier,
    s.quantity_ordered,
    s.quantity_received,
    s.quantity_rejected,
    s.quantity_received - s.quantity_rejected,
    s.order_date,
    s.promised_date,
    d.delivered_date,
    d.delivered_date - s.order_date,
    d.delivered_date - s.promised_date,
    CASE WHEN d.delivered_date IS NOT NULL THEN d.delivered_date <= s.promised_date END,
    CASE WHEN d.delivered_date IS NOT NULL
         THEN (s.quantity_received - s.quantity_rejected) >= 0.98 * s.quantity_ordered END,
    s.updated_at
FROM staging.shipments AS s
CROSS JOIN LATERAL (
    SELECT CASE WHEN s.status = 'delivered'
                THEN (s.delivered_at AT TIME ZONE %(tz)s)::date END AS delivered_date
) AS d
JOIN mart.dim_supplier AS sup ON sup.supplier_id = s.supplier_id
JOIN mart.dim_product AS p ON p.product_id = s.product_id
JOIN mart.dim_warehouse AS w ON w.warehouse_id = s.warehouse_id
WHERE s._change_seq > %(low)s AND s._change_seq <= %(high)s
ON CONFLICT (shipment_id) DO UPDATE SET
    supplier_key = EXCLUDED.supplier_key,
    product_key = EXCLUDED.product_key,
    warehouse_key = EXCLUDED.warehouse_key,
    order_date_key = EXCLUDED.order_date_key,
    promised_date_key = EXCLUDED.promised_date_key,
    delivered_date_key = EXCLUDED.delivered_date_key,
    status = EXCLUDED.status,
    carrier = EXCLUDED.carrier,
    quantity_ordered = EXCLUDED.quantity_ordered,
    quantity_received = EXCLUDED.quantity_received,
    quantity_rejected = EXCLUDED.quantity_rejected,
    quantity_accepted = EXCLUDED.quantity_accepted,
    order_date = EXCLUDED.order_date,
    promised_date = EXCLUDED.promised_date,
    delivered_date = EXCLUDED.delivered_date,
    lead_time_days = EXCLUDED.lead_time_days,
    delay_days = EXCLUDED.delay_days,
    is_on_time = EXCLUDED.is_on_time,
    is_in_full = EXCLUDED.is_in_full,
    source_updated_at = EXCLUDED.source_updated_at;
