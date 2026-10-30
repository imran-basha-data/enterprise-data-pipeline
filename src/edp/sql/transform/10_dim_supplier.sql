INSERT INTO mart.dim_supplier AS d (
    supplier_id, supplier_name, country_code, contracted_lead_time_days, is_active,
    source_updated_at
)
SELECT supplier_id, supplier_name, country_code, contracted_lead_time_days, is_active, updated_at
FROM staging.suppliers
WHERE _change_seq > %(low)s AND _change_seq <= %(high)s
ON CONFLICT (supplier_id) DO UPDATE SET
    supplier_name = EXCLUDED.supplier_name,
    country_code = EXCLUDED.country_code,
    contracted_lead_time_days = EXCLUDED.contracted_lead_time_days,
    is_active = EXCLUDED.is_active,
    source_updated_at = EXCLUDED.source_updated_at;
