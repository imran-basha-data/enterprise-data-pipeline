INSERT INTO mart.dim_product AS d (
    product_id, sku, product_name, category, unit_of_measure, unit_cost, reorder_point,
    primary_supplier_id, is_active, source_updated_at
)
SELECT product_id, sku, product_name, category, unit_of_measure, unit_cost, reorder_point,
       primary_supplier_id, is_active, updated_at
FROM staging.products
WHERE _change_seq > %(low)s AND _change_seq <= %(high)s
ON CONFLICT (product_id) DO UPDATE SET
    sku = EXCLUDED.sku,
    product_name = EXCLUDED.product_name,
    category = EXCLUDED.category,
    unit_of_measure = EXCLUDED.unit_of_measure,
    unit_cost = EXCLUDED.unit_cost,
    reorder_point = EXCLUDED.reorder_point,
    primary_supplier_id = EXCLUDED.primary_supplier_id,
    is_active = EXCLUDED.is_active,
    source_updated_at = EXCLUDED.source_updated_at;
