-- run with: mysql -u root -p < schema.sql
-- sample data below is made up

CREATE DATABASE IF NOT EXISTS smart_retail;
USE smart_retail;

DROP TABLE IF EXISTS products;

CREATE TABLE products (
    sku             VARCHAR(10)  PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    stock           INT,
    avg_daily_sales DECIMAL(8,2),
    lead_time_days  INT,             -- days the supplier takes to deliver
    expiry_days     INT,             -- days left on the current batch
    reorder_level   INT
);

-- not dropped, so loading samples keeps the login
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(30)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    recovery_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- /api/reset loads these again
INSERT INTO products (sku, name, stock, avg_daily_sales, lead_time_days, expiry_days, reorder_level) VALUES
('R101', 'Milk 1L',  8,  12, 1,   3, 10),
('R102', 'Rice 5kg', 25,  2, 3, 180,  8),
('R103', 'Bread',    4,  10, 1,   1,  6),
('R104', 'Juice',    30,  3, 2,   2, 10),
('R105', 'Yogurt',   5,   7, 1,   2,  8),
('R106', 'Soap',     40,  1, 4, 300, 10);
