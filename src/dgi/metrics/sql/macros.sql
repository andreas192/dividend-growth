CREATE OR REPLACE TEMP MACRO capped_ratio(num, den, cap) AS
    CASE WHEN num IS NULL OR den IS NULL THEN NULL
         WHEN num <= 0 THEN 0.0
         WHEN den > 0 THEN least(num / den, cap)
         ELSE cap END;
CREATE OR REPLACE TEMP MACRO cagr(first_value, last_value, years) AS
    CASE WHEN first_value > 0 AND last_value > 0 THEN pow(last_value / first_value, 1.0 / years) - 1 END;
