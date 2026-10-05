INSERT INTO company_dim BY NAME
SELECT cik, name, ticker, sic, sic_description
FROM stg_company
WHERE ticker IS NOT NULL AND ticker <> ''
QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY cik) = 1;
