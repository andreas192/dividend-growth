# Local acceptance, 2026-10-06

Re-run after the final review fixes (commit 1c23216), same method as the first run on 2026-10-05. The gold data had been rebuilt in between, so a few counts moved because of the data, not only the code.

API: `invest serve` on 127.0.0.1:8750, gold built 2026-10-06T04:02:22.176295+00:00, contract v1.

## Refresh (cold, `dgi refresh`)

- Wall time: 15.55 s
- Peak memory (`/usr/bin/time -l`): 670695424 bytes = 639.6 MiB (under the 1 GiB gate)
- Pulled rows: stg_company=16999, stg_dividends=202501, stg_splits=8674, stg_income_annual=377096, stg_cash_annual=205321, stg_balance_annual=364061, stg_shares_cover=69509, stg_income_quarter=74867, stg_cash_quarter=72039, stg_prices_latest=40498, stg_prices_yearend=424966
- Metrics: 6718 companies, 202501 dividend payments, 2440 with a streak
- Scores: 919 scored of 6718; not scored: financial_or_reit=939, insufficient_data=464, no_price=155, short_dividend_history=4241
- Checks: 6 of 6 passed (`yield_outliers`: 3 of 919 scored companies yield more than 25%)
- API requests: 32 `GET /v1/` calls for the cold refresh
- Second run, scoring edit and revert: not repeated on 2026-10-06. On 2026-10-05 the second run was `up to date` and the scoring edit `rescored`, with no `/v1/` requests (count stayed at 32 across the second run, the scoring edit, its repeat and the revert)

## Web (`dgi serve`)

- RSS idle: 87.1 MiB before the first request (114.9 MiB after the first screener request); after browsing screener, filtered screener, scatter, company pages KO, PG and KDP, two daily series, CSV, methodology, health: 139.7 MiB. These are RSS samples, not a measured maximum. The in-memory daily-series cache grows about 0.9 MB per ticker visited and is bounded at 16 entries (one long-history series is about 1.9 MB, so at most about 31 MB on top of the idle footprint). To measure the web server, note that `pgrep -f "dgi serve"` returns the `uv run` wrapper: pick the Python child process instead
- Page times: screener 18 ms, filtered screener 16 ms, company 24 ms and 9 ms, scatter 7 ms, CSV 13 ms, methodology 8 ms, health 3 ms; all 200
- Daily series, first request: 1.14 s for KO and 0.83 s for PG (about 200 KB gzipped each)
- Footer: no scoring-config drift notice (the stored scoring hash equals the loaded `config/scoring.yaml`)

## Plausibility

- KO 55, JNJ 55, PG 42 (raise streak): no cuts, streaks bounded by their history (history is 56 years; the no-cut streak of PG, KO and JNJ is 55)
- O, JPM, CB: not scored, `financial_or_reit`
- Scored 919 of 6718; pass default filters 242
- Top 10 under the default filters: HRB, BAH, ZTS, BR, AMP, MCD, CSL, DHI, INTU, KFY; none has a yield above 4.1% or an FCF payout above 71%
- Flags: `yield_outlier` (red, yield above 25%) is on 23 companies, 3 of them scored (COHN 37.4%, NHTC 42.1%, SSTK 26.2%; the `yield_outliers` check still passes with that tolerance, and the red flag now shows on those rows); `dividend_suspended` (red) is on 216 companies, 4 scored (GPRE, MTW, UFI, ZD); `suspect_dividend` (red) is on 14 companies, only KDP scored. Specials no longer count toward `suspect_dividend`, but KDP stays flagged: its 2018 payment of 103.75 (pre-merger one-off, 3 payments that year against a usual 4) is not classified special because a special needs more payments than usual, so it still exceeds the prior year-end price and inflates the 2018 annual total (104.48)
- Interest coverage: 99 is the cap. It now means no interest cost with no debt (135 of the 147 scored companies at 99 have interest expense missing and debt 0; the rest have zero interest or a true ratio at or above the cap); interest expense missing while debt exists or is unknown gives NULL (522 scored companies); operating income at or below zero gives 0. Totals: 586 companies at 99 (147 scored), 3052 NULL (522 scored)
- Other known gaps: 1176 fundamentals years use an estimated dividend (`dividends_estimated`); several scored companies land in sector "Other" (for example BAH, BR, AMP, KFY, NOC) because the SIC-to-sector map is coarse; KO's FCF payout of 166% is real data (2024-2025 FCF fell), shown with the red warning

## Kubernetes limits derived from these numbers

Peak figures are macOS `ru_maxrss` of the `uv run` process tree, so 832 Mi and 192 Mi are a starting point for a Linux container, not a guarantee.

- refresh: `limit = ceil(639.6 x 1.25 / 64) x 64 = 832 Mi` (639.6 MiB is the measured refresh peak)
- web: `limit = ceil(139.7 x 1.25 / 64) x 64 = 192 Mi` (139.7 MiB is the RSS after browsing, not a peak; the daily-series cache is bounded at 16 entries of about 2 MB each, at most about 31 MB more, which is about 171 MiB in total and fits the 192 Mi limit)

## In-cluster acceptance

NOT run. Plan Task 25 was deferred: `../investment/deploy` does not exist and there is no `invest` kubeconfig on this machine, so nothing has been deployed or verified in a cluster. Checklist for the owner, once the cluster exists:

- [ ] `DGI_INVEST_API_URL` (default `http://api.invest.svc.cluster.local:8750` in `deploy/k8s`) matches investment's actual Service name, namespace and port
- [ ] the PVC is shared by the web Deployment and the refresh CronJob/Job (access mode and node placement allow both to mount it)
- [ ] the CronJob runs (daily 07:00 Europe/Bucharest, or trigger with `scripts/deploy.sh --refresh`) and the cache swap succeeds
- [ ] `scripts/open.sh` port-forwards the UI to http://127.0.0.1:8760 and the pages load
- [ ] no OOMKill for refresh (832 Mi) or web (192 Mi); adjust the limits if the container peak differs from the macOS figures
