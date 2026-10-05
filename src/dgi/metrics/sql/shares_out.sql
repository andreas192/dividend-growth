CREATE OR REPLACE TEMP TABLE tmp_shares_out AS
SELECT r.ticker, r.value * COALESCE(f.factor, 1.0) AS shares_out
FROM (
    SELECT ticker, concept, period_end, value, filed FROM stg_shares_cover
    WHERE ticker IS NOT NULL AND value > 0 AND data_flag IS NULL
    QUALIFY row_number() OVER (
        PARTITION BY ticker
        ORDER BY period_end DESC, CASE concept WHEN 'shares_outstanding_cover' THEN 0 ELSE 1 END, filed DESC) = 1
) r
LEFT JOIN LATERAL (
    SELECT exp(sum(ln(s.ratio))) AS factor
    FROM stg_splits s WHERE s.ticker = r.ticker AND s.ex_date > r.filed AND s.ratio > 0
) f ON TRUE;
