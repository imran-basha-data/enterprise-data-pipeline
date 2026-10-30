-- Staging layer: typed, cleaned, de-duplicated current state per entity.
--
-- Every row carries lineage (_file_id, _run_id), a content hash used for
-- duplicate detection, and _change_seq: a monotonically increasing value set
-- on every insert or update. Downstream models load incrementally by reading
-- rows whose _change_seq is above their stored watermark.

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SEQUENCE staging.change_seq AS bigint;

CREATE TABLE staging.suppliers (
    supplier_id                 text PRIMARY KEY,
    supplier_name               text NOT NULL,
    country_code                char(2) NOT NULL,
    contracted_lead_time_days   integer NOT NULL CHECK (contracted_lead_time_days >= 0),
    is_active                   boolean NOT NULL,
    updated_at                  timestamptz NOT NULL,
    _record_hash                char(64) NOT NULL,
    _file_id                    bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    _run_id                     uuid NOT NULL,
    _change_seq                 bigint NOT NULL DEFAULT nextval('staging.change_seq'),
    _loaded_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staging.warehouses (
    warehouse_id                text PRIMARY KEY,
    warehouse_name              text NOT NULL,
    city                        text NOT NULL,
    region                      text,
    country_code                char(2) NOT NULL,
    capacity_units              integer CHECK (capacity_units >= 0),
    is_active                   boolean NOT NULL,
    updated_at                  timestamptz NOT NULL,
    _record_hash                char(64) NOT NULL,
    _file_id                    bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    _run_id                     uuid NOT NULL,
    _change_seq                 bigint NOT NULL DEFAULT nextval('staging.change_seq'),
    _loaded_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staging.products (
    product_id                  text PRIMARY KEY,
    sku                         text NOT NULL,
    product_name                text NOT NULL,
    category                    text NOT NULL,
    unit_of_measure             text NOT NULL,
    unit_cost                   numeric(12, 2) NOT NULL CHECK (unit_cost > 0),
    reorder_point               integer NOT NULL CHECK (reorder_point >= 0),
    primary_supplier_id         text NOT NULL REFERENCES staging.suppliers (supplier_id),
    is_active                   boolean NOT NULL,
    updated_at                  timestamptz NOT NULL,
    _record_hash                char(64) NOT NULL,
    _file_id                    bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    _run_id                     uuid NOT NULL,
    _change_seq                 bigint NOT NULL DEFAULT nextval('staging.change_seq'),
    _loaded_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staging.production_orders (
    production_order_id         text PRIMARY KEY,
    product_id                  text NOT NULL REFERENCES staging.products (product_id),
    warehouse_id                text NOT NULL REFERENCES staging.warehouses (warehouse_id),
    status                      text NOT NULL,
    planned_quantity            integer NOT NULL CHECK (planned_quantity > 0),
    produced_quantity           integer CHECK (produced_quantity >= 0),
    scrap_quantity              integer NOT NULL CHECK (scrap_quantity >= 0),
    planned_start               timestamptz NOT NULL,
    planned_end                 timestamptz NOT NULL,
    actual_start                timestamptz,
    actual_end                  timestamptz,
    updated_at                  timestamptz NOT NULL,
    _record_hash                char(64) NOT NULL,
    _file_id                    bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    _run_id                     uuid NOT NULL,
    _change_seq                 bigint NOT NULL DEFAULT nextval('staging.change_seq'),
    _loaded_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staging.inventory_movements (
    movement_id                 uuid PRIMARY KEY,
    product_id                  text NOT NULL REFERENCES staging.products (product_id),
    warehouse_id                text NOT NULL REFERENCES staging.warehouses (warehouse_id),
    movement_type               text NOT NULL,
    quantity                    integer NOT NULL CHECK (quantity <> 0),
    quantity_delta              integer NOT NULL,
    reference_id                text,
    event_timestamp             timestamptz NOT NULL,
    _record_hash                char(64) NOT NULL,
    _file_id                    bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    _run_id                     uuid NOT NULL,
    _change_seq                 bigint NOT NULL DEFAULT nextval('staging.change_seq'),
    _loaded_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staging.shipments (
    shipment_id                 text PRIMARY KEY,
    supplier_id                 text NOT NULL REFERENCES staging.suppliers (supplier_id),
    product_id                  text NOT NULL REFERENCES staging.products (product_id),
    warehouse_id                text NOT NULL REFERENCES staging.warehouses (warehouse_id),
    status                      text NOT NULL,
    carrier                     text,
    quantity_ordered            integer NOT NULL CHECK (quantity_ordered > 0),
    quantity_received           integer CHECK (quantity_received >= 0),
    quantity_rejected           integer NOT NULL CHECK (quantity_rejected >= 0),
    order_date                  date NOT NULL,
    promised_date               date NOT NULL,
    shipped_at                  timestamptz,
    delivered_at                timestamptz,
    updated_at                  timestamptz NOT NULL,
    _record_hash                char(64) NOT NULL,
    _file_id                    bigint NOT NULL REFERENCES ops.ingested_files (file_id),
    _run_id                     uuid NOT NULL,
    _change_seq                 bigint NOT NULL DEFAULT nextval('staging.change_seq'),
    _loaded_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_stg_production_orders_seq ON staging.production_orders (_change_seq);
CREATE INDEX ix_stg_inventory_movements_seq ON staging.inventory_movements (_change_seq);
CREATE INDEX ix_stg_shipments_seq ON staging.shipments (_change_seq);
