-- One row per business day, currency and status: clean business metrics.
-- Use for: daily volumes and values for reporting.
CREATE OR REPLACE VIEW v_daily_transactions AS
SELECT
    CAST(concat(year, '-', month, '-', day) AS date) AS business_date,
    currency,
    status,
    COUNT(*)                                         AS transactions,
    SUM(amount)                                      AS total_amount,
    ROUND(AVG(amount), 2)                            AS avg_amount
FROM curated_transactions
GROUP BY 1, 2, 3
