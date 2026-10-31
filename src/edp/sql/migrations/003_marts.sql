-- Analytical layer: a star schema for manufacturing and supply-chain KPIs.
--
-- Dimensions are type-1 (current state) with stable surrogate keys, so a
-- change to a supplier or product updates the dimension row in place and
-- every fact keeps pointing at the same key. Business dates are derived in
-- the configured source timezone (Europe/London by default).

CREATE SCHEMA IF NOT EXISTS mart;

-- Incremental load bookmarks: the highest staging._change_seq each model has
-- consumed. Updated in the same transaction as the model itself.
CREATE TABLE ops.model_watermarks (
    model_name          text PRIMARY KEY,
    last_change_seq     bigint NOT NULL DEFAULT 0,
    rows_affected       integer NOT NULL DEFAULT 0,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mart.dim_date (
    date_key            integer PRIMARY KEY,
    calendar_date       date NOT NULL UNIQUE,
    year                smallint NOT NULL,
    quarter             smallint NOT NULL,
    month               smallint NOT NULL,
    month_name          text NOT NULL,
    iso_year            smallint NOT NULL,
    iso_week            smallint NOT NULL,
    week_start          date NOT NULL,
    day_of_week         smallint NOT NULL,
    is_weekend          boolean NOT NULL
);

INSERT INTO mart.dim_date
SELECT
    to_char(d, 'YYYYMMDD')::integer,
    d::date,
    extract(year FROM d)::smallint,
    extract(quarter FROM d)::smallint,
    extract(month FROM d)::smallint,
    to_char(d, 'FMMonth'),
    extract(isoyear FROM d)::smallint,
    extract(week FROM d)::smallint,
    date_trunc('week', d)::date,
    extract(isodow FROM d)::smallint,
    extract(isodow FROM d) IN (6, 7)
FROM generate_series(date '2020-01-01', date '2035-12-31', interval '1 day') AS d;

CREATE TABLE mart.dim_supplier (
    supplier_key                serial PRIMARY KEY,
    supplier_id                 text NOT NULL UNIQUE,
    supplier_name               text NOT NULL,
    country_code                char(2) NOT NULL,
    contracted_lead_time_days   integer NOT NULL,
    is_active                   boolean NOT NULL,
    source_updated_at           timestamptz NOT NULL
);

CREATE TABLE mart.dim_warehouse (
    warehouse_key               serial PRIMARY KEY,
    warehouse_id                text NOT NULL UNIQUE,
    warehouse_name              text NOT NULL,
    city                        text NOT NULL,
    region                      text,
    country_code                char(2) NOT NULL,
    capacity_units              integer,
    is_active                   boolean NOT NULL,
    source_updated_at           timestamptz NOT NULL
);

CREATE TABLE mart.dim_product (
    product_key                 serial PRIMARY KEY,
    product_id                  text NOT NULL UNIQUE,
    sku                         text NOT NULL,
    product_name                text NOT NULL,
    category                    text NOT NULL,
    unit_of_measure             text NOT NULL,
    unit_cost                   numeric(12, 2) NOT NULL,
    reorder_point               integer NOT NULL,
    primary_supplier_id         text NOT NULL,
    is_active                   boolean NOT NULL,
    source_updated_at           timestamptz NOT NULL
);

-- One row per production order (accumulating snapshot).
CREATE TABLE mart.fact_production_order (
    production_order_id         text PRIMARY KEY,
    product_key                 integer NOT NULL REFERENCES mart.dim_product (product_key),
    warehouse_key               integer NOT NULL REFERENCES mart.dim_warehouse (warehouse_key),
    planned_start_date_key      integer NOT NULL REFERENCES mart.dim_date (date_key),
    completed_date_key          integer REFERENCES mart.dim_date (date_key),
    status                      text NOT NULL,
    planned_quantity            integer NOT NULL,
    produced_quantity           integer,
    scrap_quantity              integer NOT NULL,
    planned_hours               numeric(8, 2) NOT NULL,
    actual_hours                numeric(8, 2),
    start_delay_minutes         integer,
    completion_delay_minutes    integer,
    is_completed                boolean NOT NULL,
    is_on_time                  boolean,
    source_updated_at           timestamptz NOT NULL
);
CREATE INDEX ix_fact_po_completed_date ON mart.fact_production_order (completed_date_key);

-- One row per stock movement (transaction grain).
CREATE TABLE mart.fact_inventory_movement (
    movement_id                 uuid PRIMARY KEY,
    product_key                 integer NOT NULL REFERENCES mart.dim_product (product_key),
    warehouse_key               integer NOT NULL REFERENCES mart.dim_warehouse (warehouse_key),
    date_key                    integer NOT NULL REFERENCES mart.dim_date (date_key),
    movement_type               text NOT NULL,
    quantity_delta              integer NOT NULL,
    reference_id                text,
    event_timestamp             timestamptz NOT NULL
);
CREATE INDEX ix_fact_im_product_wh ON mart.fact_inventory_movement (product_key, warehouse_key);

-- One row per inbound shipment (accumulating snapshot).
CREATE TABLE mart.fact_shipment (
    shipment_id                 text PRIMARY KEY,
    supplier_key                integer NOT NULL REFERENCES mart.dim_supplier (supplier_key),
    product_key                 integer NOT NULL REFERENCES mart.dim_product (product_key),
    warehouse_key               integer NOT NULL REFERENCES mart.dim_warehouse (warehouse_key),
    order_date_key              integer NOT NULL REFERENCES mart.dim_date (date_key),
    promised_date_key           integer NOT NULL REFERENCES mart.dim_date (date_key),
    delivered_date_key          integer REFERENCES mart.dim_date (date_key),
    status                      text NOT NULL,
    carrier                     text,
    quantity_ordered            integer NOT NULL,
    quantity_received           integer,
    quantity_rejected           integer NOT NULL,
    quantity_accepted           integer,
    order_date                  date NOT NULL,
    promised_date               date NOT NULL,
    delivered_date              date,
    lead_time_days              integer,
    delay_days                  integer,
    is_on_time                  boolean,
    is_in_full                  boolean,
    source_updated_at           timestamptz NOT NULL
);
CREATE INDEX ix_fact_shipment_supplier ON mart.fact_shipment (supplier_key);
