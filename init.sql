-- Data Agent - PostgreSQL Schema Initialization
-- Run this script to set up the production database schema

-- Create tables
CREATE TABLE IF NOT EXISTS fct_orders (
    order_id    TEXT PRIMARY KEY,
    store_id    TEXT NOT NULL,
    product_id  TEXT NOT NULL,
    sell_through REAL NOT NULL,
    channel     TEXT NOT NULL,
    order_status TEXT NOT NULL,
    paid_at     TEXT NOT NULL,
    user_id     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_store (
    store_id   TEXT PRIMARY KEY,
    store_name TEXT NOT NULL,
    region     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_product (
    product_id   TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL,
    unit_price   REAL NOT NULL
);

-- Create indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_fct_orders_store_id ON fct_orders(store_id);
CREATE INDEX IF NOT EXISTS idx_fct_orders_product_id ON fct_orders(product_id);
CREATE INDEX IF NOT EXISTS idx_fct_orders_channel ON fct_orders(channel);
CREATE INDEX IF NOT EXISTS idx_fct_orders_order_status ON fct_orders(order_status);
CREATE INDEX IF NOT EXISTS idx_fct_orders_paid_at ON fct_orders(paid_at);
CREATE INDEX IF NOT EXISTS idx_dim_store_region ON dim_store(region);
CREATE INDEX IF NOT EXISTS idx_dim_product_category ON dim_product(category);

-- Insert sample data for testing
INSERT INTO dim_store (store_id, store_name, region) VALUES
('S001', '华东1号门店', '华东'),
('S002', '华东2号门店', '华东'),
('S003', '华南1号门店', '华南'),
('S004', '华北1号门店', '华北'),
('S005', '西南1号门店', '西南')
ON CONFLICT (store_id) DO NOTHING;

INSERT INTO dim_product (product_id, product_name, category, unit_price) VALUES
('P0001', '女装-连衣裙', '女装', 299.00),
('P0002', '男装-衬衫', '男装', 199.00),
('P0003', '数码-蓝牙耳机', '数码', 399.00),
('P0004', '家居-四件套', '家居', 599.00),
('P0005', '美妆-口红', '美妆', 159.00)
ON CONFLICT (product_id) DO NOTHING;
