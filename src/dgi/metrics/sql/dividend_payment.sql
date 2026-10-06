INSERT INTO dividend_payment BY NAME
WITH adj AS (
    SELECT d.ticker, d.ex_date, year(d.ex_date) AS year,
           d.amount / COALESCE(f.factor, 1.0) AS amount_adj
    FROM stg_dividends d
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stg_splits s
        WHERE s.ticker = d.ticker AND s.ex_date > d.ex_date AND s.ratio > 0
    ) f ON TRUE
    WHERE d.ticker IS NOT NULL AND d.amount > 0 AND d.ex_date <= (SELECT today FROM run_params)
),
counts AS (
    SELECT ticker, year, count(*) AS n FROM adj GROUP BY ticker, year
),
usual AS (
    SELECT ticker, n AS usual_n
    FROM (
        SELECT ticker, n, count(*) AS freq
        FROM counts
        WHERE year < year((SELECT today FROM run_params))
        GROUP BY ticker, n
    )
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY freq DESC, n ASC) = 1
),
ranked AS (
    SELECT a.ticker, a.ex_date, a.year, a.amount_adj, c.n, u.usual_n,
           row_number() OVER (PARTITION BY a.ticker, a.year ORDER BY a.amount_adj DESC, a.ex_date) AS rk
    FROM adj a
    JOIN counts c ON c.ticker = a.ticker AND c.year = a.year
    LEFT JOIN usual u ON u.ticker = a.ticker
),
others AS (
    SELECT ticker, year, median(amount_adj) AS others_median
    FROM ranked WHERE rk > 1 GROUP BY ticker, year
)
SELECT r.ticker, r.ex_date, r.year, r.amount_adj,
       COALESCE(
           r.rk = 1 AND r.n > COALESCE(r.usual_n, r.n)
           AND r.amount_adj >= (SELECT special_ratio FROM run_params) * o.others_median,
           FALSE) AS is_special
FROM ranked r
LEFT JOIN others o ON o.ticker = r.ticker AND o.year = r.year;
