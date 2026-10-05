INSERT INTO dividend_annual BY NAME
SELECT ticker, year,
       sum(amount_adj) FILTER (WHERE NOT is_special) AS dps,
       count(*) FILTER (WHERE NOT is_special) AS n_payments,
       COALESCE(sum(amount_adj) FILTER (WHERE is_special), 0.0) AS special_total,
       year < year((SELECT today FROM run_params)) AS complete
FROM dividend_payment
GROUP BY ticker, year
HAVING count(*) FILTER (WHERE NOT is_special) > 0;
