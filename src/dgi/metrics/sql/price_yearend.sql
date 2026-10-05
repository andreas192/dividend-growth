INSERT INTO price_yearend BY NAME
SELECT p.ticker, year(p.trade_date) AS year, p.close / COALESCE(f.factor, 1.0) AS close_adj
FROM (
    SELECT ticker, trade_date, close FROM stg_prices_yearend
    WHERE ticker IS NOT NULL AND close > 0
    QUALIFY row_number() OVER (PARTITION BY ticker, year(trade_date) ORDER BY trade_date DESC) = 1
) p
LEFT JOIN LATERAL (
    SELECT exp(sum(ln(s.ratio))) AS factor
    FROM stg_splits s
    WHERE s.ticker = p.ticker AND s.ex_date > p.trade_date AND s.ratio > 0
) f ON TRUE;
