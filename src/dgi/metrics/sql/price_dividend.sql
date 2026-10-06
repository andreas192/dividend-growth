CREATE OR REPLACE TEMP TABLE tmp_price AS
SELECT ticker, close AS price, trade_date AS price_date
FROM stg_prices_latest
WHERE ticker IS NOT NULL AND close > 0
QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY trade_date DESC) = 1;

CREATE OR REPLACE TEMP TABLE tmp_dividend_ttm AS
SELECT ticker,
       sum(amount_adj) FILTER (WHERE NOT is_special AND ex_date > (SELECT today FROM run_params) - 365) AS dividend_ttm,
       count(*) FILTER (WHERE is_special AND year >= year((SELECT today FROM run_params)) - 5)::INTEGER AS special_count_5y
FROM dividend_payment
GROUP BY ticker;

CREATE OR REPLACE TEMP TABLE tmp_yield_avg AS
SELECT a.ticker, avg(a.dps / y.close_adj) AS div_yield_avg_5y
FROM dividend_annual a
JOIN price_yearend y ON y.ticker = a.ticker AND y.year = a.year
WHERE a.complete AND a.year >= year((SELECT today FROM run_params)) - 5
GROUP BY a.ticker
HAVING count(*) >= 3;

CREATE OR REPLACE TEMP TABLE tmp_suspect AS
SELECT p.ticker, count(*)::INTEGER AS suspect_dividend_count
FROM dividend_payment p
JOIN price_yearend y ON y.ticker = p.ticker AND y.year = p.year - 1
WHERE NOT p.is_special AND p.amount_adj >= y.close_adj  -- specials raise their own info flag
GROUP BY p.ticker;
