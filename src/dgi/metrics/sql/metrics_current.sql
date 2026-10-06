INSERT INTO metrics_current BY NAME
WITH joined AS (
    SELECT c.ticker, p.price, p.price_date, p.price * so.shares_out AS market_cap,
           dt.dividend_ttm, dt.dividend_ttm / p.price AS div_yield, ya.div_yield_avg_5y,
           (dt.dividend_ttm / p.price) / ya.div_yield_avg_5y - 1 AS yield_vs_avg,
           COALESCE(dt.special_count_5y, 0) AS special_count_5y,
           COALESCE(sp.suspect_dividend_count, 0) AS suspect_dividend_count,
           dm.streak, dm.no_cut_streak, dm.dgr_1, dm.dgr_3, dm.dgr_5, dm.dgr_10, dm.years_history,
           dm.payment_frequency, dm.cut_years_5y, dm.irregular_payments,
           fm.latest_fy, fm.fy_period_end, fm.payout_earnings, fm.payout_earnings_5y, fm.payout_fcf, fm.payout_fcf_5y,
           fm.interest_coverage, fm.net_debt_ebitda, fm.current_ratio, fm.positive_earnings_years,
           fm.rev_cagr_5, fm.eps_cagr_5, fm.fcf_ps_cagr_5, fm.roe, fm.op_margin_std, fm.share_trend_5,
           fm.fcf_latest, fm.net_income_latest,
           (t.ttm_end IS NOT NULL AND t.ttm_end > fm.fy_period_end AND t.shares_ttm > 0) AS use_ttm,
           t.ni_ttm / NULLIF(t.shares_ttm, 0) AS eps_ttm,
           (t.cfo_ttm - t.capex_ttm) / NULLIF(t.shares_ttm, 0) AS fcf_ps_ttm,
           fm.eps_fy, fm.fcf_ps_fy
    FROM company_dim c
    LEFT JOIN tmp_price p ON p.ticker = c.ticker
    LEFT JOIN tmp_shares_out so ON so.ticker = c.ticker
    LEFT JOIN tmp_dividend_ttm dt ON dt.ticker = c.ticker
    LEFT JOIN tmp_yield_avg ya ON ya.ticker = c.ticker
    LEFT JOIN tmp_suspect sp ON sp.ticker = c.ticker
    LEFT JOIN tmp_dividend_metrics dm ON dm.ticker = c.ticker
    LEFT JOIN tmp_fund_metrics fm ON fm.ticker = c.ticker
    LEFT JOIN tmp_ttm t ON t.ticker = c.ticker
),
based AS (
    SELECT *,
           CASE WHEN use_ttm THEN 'ttm' WHEN latest_fy IS NOT NULL THEN 'fy' END AS basis,
           CASE WHEN use_ttm THEN eps_ttm ELSE eps_fy END AS eps_basis,
           CASE WHEN use_ttm THEN fcf_ps_ttm ELSE fcf_ps_fy END AS fcf_ps_basis
    FROM joined
)
SELECT ticker, price, price_date, market_cap, dividend_ttm, div_yield, div_yield_avg_5y, yield_vs_avg,
       streak, no_cut_streak, dgr_1, dgr_3, dgr_5, dgr_10, years_history, payment_frequency, cut_years_5y,
       irregular_payments, special_count_5y, suspect_dividend_count, latest_fy, fy_period_end, basis,
       eps_basis, fcf_ps_basis, payout_earnings, payout_earnings_5y, payout_fcf, payout_fcf_5y,
       interest_coverage, net_debt_ebitda, current_ratio, positive_earnings_years,
       rev_cagr_5, eps_cagr_5, fcf_ps_cagr_5, roe, op_margin_std, share_trend_5,
       CASE WHEN eps_basis > 0 THEN price / eps_basis END AS pe,
       CASE WHEN fcf_ps_basis > 0 THEN price / fcf_ps_basis END AS p_fcf,
       CASE WHEN price > 0 THEN fcf_ps_basis / price END AS fcf_yield,
       fcf_latest, net_income_latest
FROM based;
