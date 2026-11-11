-- One row per business day and failed rule: which problems happen most.
-- Use for: "Which rule should the source team fix first?"
CREATE OR REPLACE VIEW v_rule_failures_daily AS
SELECT
    CAST(concat(r.year, '-', r.month, '-', r.day) AS date) AS business_date,
    f.rule_id,
    f.rule_name,
    SUM(f.failed_rows)                                     AS failed_rows
FROM dq_reports AS r
CROSS JOIN UNNEST(r.rule_failures) AS t(f)
WHERE f.failed_rows > 0
GROUP BY 1, 2, 3
