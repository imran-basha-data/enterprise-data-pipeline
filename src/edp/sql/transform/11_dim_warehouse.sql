INSERT INTO mart.dim_warehouse AS d (
    warehouse_id, warehouse_name, city, region, country_code, capacity_units, is_active,
    source_updated_at
)
SELECT warehouse_id, warehouse_name, city, region, country_code, capacity_units, is_active,
       updated_at
FROM staging.warehouses
WHERE _change_seq > %(low)s AND _change_seq <= %(high)s
ON CONFLICT (warehouse_id) DO UPDATE SET
    warehouse_name = EXCLUDED.warehouse_name,
    city = EXCLUDED.city,
    region = EXCLUDED.region,
    country_code = EXCLUDED.country_code,
    capacity_units = EXCLUDED.capacity_units,
    is_active = EXCLUDED.is_active,
    source_updated_at = EXCLUDED.source_updated_at;
