CREATE OR REPLACE TEMP TABLE tmp_ttm AS
WITH quarters AS (
    SELECT ticker, concept, period_end, value, filed FROM stg_income_quarter WHERE data_flag IS NULL
    UNION ALL
    SELECT ticker, concept, period_end, value, filed FROM stg_cash_quarter WHERE data_flag IS NULL
),
ranked AS (
    SELECT ticker, concept, period_end, value, filed,
           row_number() OVER (PARTITION BY ticker, concept ORDER BY period_end DESC) AS rn
    FROM quarters
    WHERE ticker IS NOT NULL AND value IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY ticker, concept, period_end ORDER BY filed DESC) = 1
),
sums AS (
    SELECT ticker, concept, sum(value) AS total, max(period_end) AS last_end,
           date_diff('day', min(period_end), max(period_end)) AS span
    FROM ranked WHERE rn <= 4 AND concept IN ('net_income', 'cash_from_operations', 'capex')
    GROUP BY ticker, concept HAVING count(*) = 4
),
wide AS (
    SELECT ticker, max(last_end) AS ttm_end,
           count(DISTINCT last_end) AS distinct_ends,
           max(total) FILTER (WHERE concept = 'net_income') AS ni_ttm,
           max(total) FILTER (WHERE concept = 'cash_from_operations') AS cfo_ttm,
           abs(max(total) FILTER (WHERE concept = 'capex')) AS capex_ttm,
           count(*) AS n_concepts,
           min(span) AS min_span, max(span) AS max_span
    FROM sums GROUP BY ticker
),
shares AS (
    SELECT r.ticker, r.period_end, r.value * COALESCE(f.factor, 1.0) AS shares_ttm
    FROM ranked r
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stg_splits s WHERE s.ticker = r.ticker AND s.ex_date > r.filed AND s.ratio > 0
    ) f ON TRUE
    WHERE r.concept = 'shares_diluted_weighted' AND r.rn = 1
)
SELECT w.ticker, w.ttm_end, w.ni_ttm, w.cfo_ttm, w.capex_ttm, s.shares_ttm
FROM wide w
JOIN shares s ON s.ticker = w.ticker AND s.period_end = w.ttm_end
WHERE w.n_concepts = 3 AND w.distinct_ends = 1
  AND w.min_span >= (SELECT ttm_min_span_days FROM run_params)
  AND w.max_span <= (SELECT ttm_max_span_days FROM run_params);
