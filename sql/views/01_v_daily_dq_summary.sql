-- One row per business day: how much data arrived and how clean it was.
-- Use for: "Is data quality getting better or worse over time?"
CREATE OR REPLACE VIEW v_daily_dq_summary AS
SELECT
    CAST(concat(year, '-', month, '-', day) AS date)                         AS business_date,
    COUNT(*)                                                                  AS files_processed,
    SUM(total_rows)                                                           AS total_rows,
    SUM(valid_rows)                                                           AS valid_rows,
    SUM(rejected_rows)                                                        AS rejected_rows,
    ROUND(CAST(SUM(rejected_rows) AS double) / NULLIF(SUM(total_rows), 0), 4) AS reject_rate,
    COUNT_IF(status = 'PASSED_WITH_WARNINGS')                                 AS files_with_warnings
FROM dq_reports
GROUP BY year, month, day
