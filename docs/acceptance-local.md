# Local acceptance, 2026-10-05

API: `invest serve` on 127.0.0.1:8750, gold built 2026-10-05T16:54:24.390595+00:00, contract v1.

## Refresh (cold, `dgi refresh`)

- Wall time: 14.11 s
- Peak memory (`/usr/bin/time -l`): 665026560 bytes = 634.2 MiB (under the 1 GiB gate)
- Pulled rows: stg_company=16999, stg_dividends=202472, stg_splits=8664, stg_income_annual=377102, stg_cash_annual=205328, stg_balance_annual=364052, stg_shares_cover=69511, stg_income_quarter=74887, stg_cash_quarter=72061, stg_prices_latest=40911, stg_prices_yearend=424733
- Metrics: 6716 companies, 202472 dividend payments, 2439 with a streak
- Scores: 926 scored of 6716; not scored: financial_or_reit=939, insufficient_data=458, no_price=158, short_dividend_history=4235
- Checks: 6 of 6 passed
- API requests: 32 `GET /v1/` calls for the cold refresh
- Second run: `up to date`; scoring edit: `rescored`, no `/v1/` requests (count stayed at 32 across the second run, the scoring edit, its repeat and the revert)

## Web (`dgi serve`)

- RSS idle: 86.8 MiB; after browsing screener, two company pages, daily series, CSV: 132.2 MiB (122.2 MiB after the two headless Chrome screenshots that followed)
- Page times: screener 37 ms, filtered screener 14 ms, company 22 ms and 9 ms, scatter 6 ms, CSV 13 ms, methodology 7 ms, health 3 ms; all 200
- Daily series, first request: 0.88 s (195.8 KB gzipped, 859.7 KB uncompressed)

## Plausibility

- KO 55, JNJ 55, PG 42: no cuts, streaks bounded by their history (history is 56 years; PG's no-cut streak is 55, its raise streak 42)
- O, JPM, CB: not scored, `financial_or_reit`
- Scored 926 of 6716; pass default filters 242
- Top 10 under the default filters: BAH, HRB, ZTS, BR, AMP, MCD, INTU, CSL, DHI, KFY; none has a yield above 4.1% or an FCF payout above 71%
- Known feed gaps seen: 3 scored companies yield above 25% (COHN 37.8%, NHTC 40.0%, SSTK 27.6%; the check passes with that tolerance); KDP carries the red `suspect_dividend` flag (a dividend at or above the share price in the price data); 1182 fundamentals years use an estimated dividend (`dividends_estimated`); several scored companies land in sector "Other" (for example BAH, BR, AMP, KFY, NOC) because the SIC-to-sector map is coarse; KO's FCF payout of 166% is real data (2024-2025 FCF fell), shown with the red warning

## Kubernetes limits derived from these numbers

- refresh: `limit = ceil(634.2 x 1.25 / 64) x 64 = 832 Mi`
- web: `limit = ceil(132.2 x 1.25 / 64) x 64 = 192 Mi`
