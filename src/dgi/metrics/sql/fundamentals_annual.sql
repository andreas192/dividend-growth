INSERT INTO fundamentals_annual BY NAME
WITH facts AS (
    SELECT ticker, fiscal_year, concept, value, filed, period_end FROM stg_income_annual WHERE data_flag IS NULL
    UNION ALL
    SELECT ticker, fiscal_year, concept, value, filed, period_end FROM stg_cash_annual WHERE data_flag IS NULL
    UNION ALL
    SELECT ticker, fiscal_year, concept, value, filed, period_end FROM stg_balance_annual WHERE data_flag IS NULL
),
clean AS (
    SELECT * FROM facts
    WHERE ticker IS NOT NULL AND value IS NOT NULL AND fiscal_year IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY ticker, concept, fiscal_year ORDER BY period_end DESC, filed DESC) = 1
),
per_share AS (
    SELECT c.ticker, c.fiscal_year, c.concept, c.period_end,
           CASE c.concept WHEN 'eps_diluted' THEN c.value / COALESCE(f.factor, 1.0)
                          ELSE c.value * COALESCE(f.factor, 1.0) END AS value
    FROM clean c
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stg_splits s
        WHERE s.ticker = c.ticker AND s.ex_date > c.filed AND s.ratio > 0
    ) f ON TRUE
    WHERE c.concept IN ('eps_diluted', 'shares_diluted_weighted')
),
adjusted AS (
    SELECT ticker, fiscal_year, concept, period_end, value FROM clean
    WHERE concept NOT IN ('eps_diluted', 'shares_diluted_weighted')
    UNION ALL
    SELECT ticker, fiscal_year, concept, period_end, value FROM per_share
),
wide AS (
    SELECT ticker, fiscal_year, max(period_end) AS period_end,
           max(value) FILTER (WHERE concept = 'revenue') AS revenue,
           max(value) FILTER (WHERE concept = 'net_income') AS net_income,
           max(value) FILTER (WHERE concept = 'operating_income') AS operating_income,
           max(value) FILTER (WHERE concept = 'eps_diluted') AS eps_diluted,
           max(value) FILTER (WHERE concept = 'shares_diluted_weighted') AS shares_diluted,
           max(value) FILTER (WHERE concept = 'interest_expense') AS interest_expense,
           max(value) FILTER (WHERE concept = 'cash_from_operations') AS cfo,
           abs(max(value) FILTER (WHERE concept = 'capex')) AS capex,
           abs(max(value) FILTER (WHERE concept = 'dividends_paid')) AS dividends_paid_reported,
           max(value) FILTER (WHERE concept = 'depreciation_amortization') AS depreciation_amortization,
           max(value) FILTER (WHERE concept = 'total_equity') AS equity,
           max(value) FILTER (WHERE concept = 'current_assets') AS current_assets,
           max(value) FILTER (WHERE concept = 'current_liabilities') AS current_liabilities,
           max(value) FILTER (WHERE concept = 'cash_and_equivalents') AS cash,
           max(value) FILTER (WHERE concept = 'short_term_investments') AS short_term_investments,
           max(value) FILTER (WHERE concept = 'long_term_debt') AS long_term_debt,
           max(value) FILTER (WHERE concept = 'short_term_debt') AS short_term_debt,
           max(value) FILTER (WHERE concept = 'current_portion_long_term_debt') AS current_portion_debt,
           count(*) FILTER (WHERE concept IN ('total_equity', 'current_assets', 'current_liabilities', 'cash_and_equivalents')) > 0 AS balance_reported
    FROM adjusted
    GROUP BY ticker, fiscal_year
),
base AS (
    -- Some filers report no cash dividends paid (JNJ, for one). The estimate is that fiscal year's regular dividend per
    -- share times diluted shares, both on today's share basis; `dividends_estimated` records where it was used.
    SELECT w.*,
           COALESCE(w.dividends_paid_reported, d.dps * w.shares_diluted) AS dividends_paid,
           (w.dividends_paid_reported IS NULL AND d.dps IS NOT NULL AND w.shares_diluted IS NOT NULL) AS dividends_estimated,
           w.cfo - w.capex AS fcf,
           CASE WHEN w.balance_reported THEN
               COALESCE(w.long_term_debt, 0) + COALESCE(w.short_term_debt, 0) + COALESCE(w.current_portion_debt, 0) END AS debt
    FROM wide w
    LEFT JOIN dividend_annual d ON d.ticker = w.ticker AND d.year = w.fiscal_year AND d.complete
)
SELECT ticker, fiscal_year, period_end, revenue, net_income, operating_income, eps_diluted, shares_diluted,
       interest_expense, cfo, capex, dividends_paid, depreciation_amortization, fcf,
       dividends_estimated, equity, current_assets, current_liabilities, cash, short_term_investments, debt,
       CASE WHEN debt IS NOT NULL AND cash IS NOT NULL THEN debt - cash - COALESCE(short_term_investments, 0) END AS net_debt,
       operating_income + depreciation_amortization AS ebitda,
       operating_income / NULLIF(revenue, 0) AS op_margin,
       capped_ratio(dividends_paid, net_income, (SELECT payout_cap FROM run_params)) AS payout_earnings,
       capped_ratio(dividends_paid, fcf, (SELECT payout_cap FROM run_params)) AS payout_fcf,
       fcf / NULLIF(shares_diluted, 0) AS fcf_per_share
FROM base;
