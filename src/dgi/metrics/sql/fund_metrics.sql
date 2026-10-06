CREATE OR REPLACE TEMP TABLE tmp_fund_metrics AS
WITH latest AS (
    SELECT ticker, fiscal_year AS fy FROM fundamentals_annual
    WHERE net_income IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY fiscal_year DESC) = 1
),
cur AS (
    SELECT f.*, l.fy FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND l.fy = f.fiscal_year
),
ago AS (
    SELECT f.ticker, f.revenue, f.eps_diluted, f.fcf_per_share, f.shares_diluted
    FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND l.fy - 5 = f.fiscal_year
),
win5 AS (
    SELECT f.ticker,
           capped_ratio(sum(f.dividends_paid), sum(f.net_income), (SELECT payout_cap FROM run_params)) AS payout_earnings_5y,
           capped_ratio(sum(f.dividends_paid), sum(f.fcf), (SELECT payout_cap FROM run_params)) AS payout_fcf_5y,
           CASE WHEN count(f.op_margin) >= 3 THEN stddev_pop(f.op_margin) END AS op_margin_std
    FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND f.fiscal_year BETWEEN l.fy - 4 AND l.fy
    GROUP BY f.ticker
),
win10 AS (
    SELECT f.ticker, count(*) FILTER (WHERE f.net_income > 0)::INTEGER AS positive_earnings_years
    FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND f.fiscal_year BETWEEN l.fy - 9 AND l.fy
    GROUP BY f.ticker
)
SELECT c.ticker, c.fy AS latest_fy, c.period_end AS fy_period_end,
       c.payout_earnings, c.payout_fcf, w5.payout_earnings_5y, w5.payout_fcf_5y, w5.op_margin_std,
       CASE WHEN c.operating_income IS NULL THEN NULL
            WHEN c.operating_income <= 0 THEN 0.0
            WHEN c.interest_expense IS NULL AND COALESCE(c.debt, 1) > 0 THEN NULL  -- interest unknown while debt exists or is unknown
            WHEN COALESCE(c.interest_expense, 0) <= 0 THEN (SELECT ratio_cap FROM run_params)
            ELSE least(c.operating_income / c.interest_expense, (SELECT ratio_cap FROM run_params)) END AS interest_coverage,
       CASE WHEN c.net_debt IS NULL OR c.ebitda IS NULL THEN NULL
            WHEN c.net_debt <= 0 THEN 0.0
            WHEN c.ebitda <= 0 THEN (SELECT ratio_cap FROM run_params)
            ELSE least(c.net_debt / c.ebitda, (SELECT ratio_cap FROM run_params)) END AS net_debt_ebitda,
       c.current_assets / NULLIF(c.current_liabilities, 0) AS current_ratio,
       w10.positive_earnings_years,
       cagr(a.revenue, c.revenue, 5) AS rev_cagr_5,
       cagr(a.eps_diluted, c.eps_diluted, 5) AS eps_cagr_5,
       cagr(a.fcf_per_share, c.fcf_per_share, 5) AS fcf_ps_cagr_5,
       CASE WHEN c.equity > 0 THEN c.net_income / c.equity END AS roe,
       cagr(a.shares_diluted, c.shares_diluted, 5) AS share_trend_5,
       c.net_income / NULLIF(c.shares_diluted, 0) AS eps_fy,
       c.fcf_per_share AS fcf_ps_fy,
       c.fcf AS fcf_latest, c.net_income AS net_income_latest
FROM cur c
LEFT JOIN ago a ON a.ticker = c.ticker
LEFT JOIN win5 w5 ON w5.ticker = c.ticker
LEFT JOIN win10 w10 ON w10.ticker = c.ticker;
