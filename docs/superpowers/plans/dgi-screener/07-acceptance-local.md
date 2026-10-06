# Phase 7: Local acceptance against the real API (Task 23)

Part of `../2026-10-05-dgi-screener.md`. Spec: `../../specs/2026-10-05-dgi-screener-design.md`, sections "Success", "Deployment" (resource rule and "Local first") and "Testing".

This task runs the finished app against the real local investment API, measures it, and records the numbers the Kubernetes limits are set from. **It is the gate for Tasks 24-26**: if the refresh peak exceeds 1 GiB, stop and redesign the pulls (smaller pages, fewer concepts) and update the spec before writing manifests.

## Expected shape (a dry run of this design, 2026-10-05)

The design was run end to end on a scratch copy of this code against the local API on the day the plan was written. Numbers move with the data, so compare shape, not digits:

| Measure | Dry run |
|---|---|
| Refresh, cold | 14-15 s; peak memory 676-697 MB (about 650 MiB); checks 6 of 6 |
| Pulled | about 17k companies, 202k dividend events, 8.7k splits, 377k annual income, 205k annual cash flow, 364k annual balance sheet, 425k year-end prices |
| Universe | 6,716 companies with a ticker; about 926 scored; not scored: `financial_or_reit` about 940, `insufficient_data` about 460, `no_price` about 160, `short_dividend_history` about 4,200 |
| Pass the default filters | about 240 |
| KO, JNJ | streak `55+` (the data's dividend history is 56 years, from about 1970; the real records are longer), no cuts; KO payout of FCF 166% with the red warning (its free cash flow dipped); JNJ payout of FCF about 63% (reported dividends paid are missing, so the estimate is used) |
| PG, PEP | streak 42 (PG: no-cut streak 55; an old flat year in the source), 53 |
| MMM, T | streak 0 with cuts inside the last five years (MMM's 2024 cut, T's 2022 cut) |
| O (REIT), JPM (bank), CB (insurer) | `not_scored`, `financial_or_reit` |
| XOM | `insufficient_data`: gold has no recent statements for it (an investment data gap, not a dgi bug) |
| Web app memory | about 90 MB idle, about 125 MB after browsing; the daily series of a 60-year company takes about 0.9 s the first time and is 200 KB gzipped |

Known gaps in the data feed that make some companies score on less data (report them to the investment project, do not work around them here): VZ has no `capex` concept (no FCF payout), XOM has no recent statements, foreign 20-F filers (CCEP, IMO, RELX) and some BDCs have no US-GAAP statements.

---

### Task 23: Run against the real API, measure, record, and update the spec

**Files:**
- Create: `docs/acceptance-local.md`
- Modify: `docs/superpowers/specs/2026-10-05-dgi-screener-design.md` (apply the plan's clarifications), `CLAUDE.md` (status line)

**Interfaces:**
- Consumes: the complete app (Tasks 1-22) and the local API on `127.0.0.1:8750`.
- Produces: `docs/acceptance-local.md` with the measured refresh peak, web memory and counts; the memory limits for Task 24 (`limit = peak MiB x 1.25, rounded up to a multiple of 64 MiB`).

- [ ] **Step 1: Start the API read-only**

Use investment's own virtualenv binary directly. `uv run` inside `../investment` could sync that project's environment, which this project must not touch.

```bash
(cd ../investment && nohup .venv/bin/invest serve > /tmp/invest-serve.log 2>&1 &)
sleep 3
curl -s localhost:8750/health
curl -s localhost:8750/contracts
```
Expected: `/health` shows a `content_hash` and a `gold_built_at`; `/contracts` shows `"current":"v1"`. If the API answers 503, gold is not built (`invest build-gold` in investment is the owner's call, not this project's).

- [ ] **Step 2: Cold refresh, with memory measured**

```bash
rm -f data/dgi.duckdb data/dgi.duckdb.*
/usr/bin/time -l uv run dgi refresh 2>&1 | tee /tmp/dgi-refresh.log | grep -E "^(error|rebuilt|upstream|pulled|metrics|scores|checks|warning)|maximum resident|real"
```
Expected: `rebuilt: no usable cache`, `checks: 6 of 6 passed`, a wall time of seconds to a minute, and a `maximum resident set size` line in **bytes**.

Gate: the peak must be under 1,073,741,824 bytes (1 GiB). Convert for the record: `peak MiB = bytes / 1048576`.

If it is over the gate, stop here: reduce `DGI_PAGE_LIMIT` (default 100000), narrow the pulls in `src/dgi/client/pulls.py` (fewer years or concepts) or stage fewer tables at a time, re-measure, and update the spec's resource section. Do not continue to Task 24 until the peak fits.

- [ ] **Step 3: Acceptance checks on the real cache**

```bash
uv run python - <<'PY'
import duckdb

con = duckdb.connect("data/dgi.duckdb", read_only=True)
q = lambda sql, *a: con.execute(sql, list(a)).fetchall()
one = lambda t: con.execute(
    "SELECT s.status, s.reason, m.streak, m.no_cut_streak, m.years_history, m.cut_years_5y, round(m.payout_fcf, 2), round(s.total, 1) "
    "FROM scores s JOIN metrics_current m USING (ticker) WHERE ticker = ?", [t]).fetchone()

for t in ("KO", "JNJ", "PG"):
    status, reason, streak, no_cut, history, cuts, payout_fcf, total = one(t)
    print(t, dict(status=status, streak=streak, no_cut=no_cut, history=history, cuts=cuts, payout_fcf=payout_fcf, score=total))
    assert status == "scored" and cuts == 0 and streak >= 40, f"{t}: implausible streak or a cut"
    assert streak <= history - 1, f"{t}: streak longer than its history"
for t in ("O", "JPM", "CB"):
    assert one(t)[:2] == ("not_scored", "financial_or_reit"), f"{t} must be listed as not scored (financial_or_reit)"
scored, total = q("SELECT count(*) FILTER (WHERE status = 'scored'), count(*) FROM scores")[0]
assert scored >= 300, f"only {scored} scored companies"
print("scored", scored, "of", total, "not scored by reason:", q("SELECT reason, count(*) FROM scores WHERE status = 'not_scored' GROUP BY 1 ORDER BY 2 DESC"))
print("top 10 under the default filters:")
for r in q("""SELECT s.ticker, left(c.name, 28), round(s.total, 1), m.streak, round(m.div_yield, 3), round(m.payout_fcf, 2)
              FROM scores s JOIN metrics_current m USING (ticker) JOIN company_dim c USING (ticker)
              WHERE s.status = 'scored' AND m.streak >= 5 AND m.market_cap >= 1e9 AND coalesce(m.payout_fcf, 9) <= 1
              ORDER BY s.total DESC LIMIT 10"""):
    print(" ", r)
print("flags:", q("SELECT code, count(*) FROM flags GROUP BY 1 ORDER BY 2 DESC"))
print("coverage of scored (min, median):", q("SELECT round(min(coverage), 2), round(median(coverage), 2) FROM scores WHERE status = 'scored'"))
PY
uv run dgi status
uv run dgi check
```
Expected: the asserts pass, `dgi status` prints the counts, and `dgi check` prints six `PASS` lines. KO, JNJ and PG streaks are history-limited (a streak equal to history minus one is normal for KO and JNJ). Read the top-10 list: a company that looks wrong (a yield above 15%, a payout above 100% inside the top ten) is a data question to trace to `dividend_annual` / `fundamentals_annual` before moving on; fix a real bug with a failing test first.

- [ ] **Step 4: A second refresh does nothing, and a scoring edit does not call the API**

```bash
uv run dgi refresh                       # expect: up to date
grep -c "GET /v1/" /tmp/invest-serve.log  # note the number
cp config/scoring.yaml /tmp/scoring-test.yaml
sed -i '' 's/min_streak: 5/min_streak: 7/' /tmp/scoring-test.yaml
DGI_SCORING_CONFIG=/tmp/scoring-test.yaml uv run dgi refresh   # expect: rescored: scoring config changed
grep -c "GET /v1/" /tmp/invest-serve.log  # expect: the same number
DGI_SCORING_CONFIG=/tmp/scoring-test.yaml uv run dgi refresh   # expect: up to date
uv run dgi refresh                       # the repo config differs again: rescored back
```
Expected: the `GET /v1/` count does not change across the last four commands (only `/health` and `/contracts` are asked).

- [ ] **Step 5: Serve, browse, measure the web app**

```bash
uv run dgi serve --port 8760 &
sleep 4
WEB=$(pgrep -f "dgi serve" | head -1)
ps -o rss= -p "$WEB"                       # idle, KiB
for u in "/" "/?sector=Utilities&min_streak=10" "/company/KO" "/company/JNJ" "/api/company/KO/daily" "/api/scatter" "/screener.csv" "/methodology" "/health"; do
  curl -s -m 30 -o /dev/null -w "%{http_code} %{time_total}s $u\n" "http://127.0.0.1:8760$u"
done
ps -o rss= -p "$WEB"                       # after browsing, KiB
```
Expected: every status is 200; the pages answer in tens of milliseconds, the daily endpoint in about a second the first time. Record both RSS values in MiB (`KiB / 1024`).

- [ ] **Step 6: Look at the real pages**

Screenshot the screener and a long-history company (KO) as in Task 21 Step C5, with the server from Step 5 (port 8760):

```bash
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CH" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=9000 --window-size=1300,2700 --screenshot=/tmp/dgi-ko.png "http://127.0.0.1:8760/company/KO"
"$CH" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=9000 --window-size=1300,1700 --screenshot=/tmp/dgi-screener-real.png "http://127.0.0.1:8760/"
kill "$WEB"
```
Check the same list as before and, specifically for real data: the streak shows `55+` for KO, the red warning reads in words, the 60-year price and yield charts draw, nothing overflows horizontally at 1300 px. Fix real layout problems in `app.css` / `charts.js` with the browser open; the unit tests do not cover layout.

- [ ] **Step 7: Record the measurements**

Create `docs/acceptance-local.md` with the numbers you actually saw (replace every `<...>` with a measured value; none may stay):

```markdown
# Local acceptance, <date>

API: `invest serve` on 127.0.0.1:8750, gold built <gold_built_at from /health>, contract v1.

## Refresh (cold, `dgi refresh`)

- Wall time: <seconds> s
- Peak memory (`/usr/bin/time -l`): <bytes> bytes = <MiB> MiB
- Pulled rows: <the `pulled:` line>
- Metrics: <the `metrics:` line>
- Scores: <the `scores:` line>
- Checks: 6 of 6 passed
- Second run: `up to date`; scoring edit: `rescored`, no `/v1/` requests

## Web (`dgi serve`)

- RSS idle: <MiB> MiB; after browsing screener, two company pages, daily series, CSV: <MiB> MiB
- Daily series, first request: <seconds> s (<KB> KB gzipped)

## Plausibility

- KO <streak>, JNJ <streak>, PG <streak>: no cuts, streaks bounded by their history
- O, JPM, CB: not scored, `financial_or_reit`
- Scored <n> of <n>; pass default filters <n>
- Known feed gaps seen: <list>

## Kubernetes limits derived from these numbers

- refresh: `limit = ceil(<peak MiB> x 1.25 / 64) x 64 = <n> Mi`
- web: `limit = ceil(<web peak MiB> x 1.25 / 64) x 64 = <n> Mi`
```

- [ ] **Step 8: Update the spec**

CLAUDE.md asks that the spec change instead of silently diverging. Edit `docs/superpowers/specs/2026-10-05-dgi-screener-design.md` as follows (the plan's "Spec clarifications" list in `2026-10-05-dgi-screener.md` has the reasons):

| Where in the spec | Change |
|---|---|
| Header `Status:` | "Approved 2026-10-05; clarified by the implementation plan and the local acceptance run." |
| Metrics table, Safety row | Append: "Payout ratios are computed on dollars (dividends paid over net income and over FCF), capped at 999%; when a filer reports no dividends paid, the year's regular dividend per share times diluted shares is used." |
| Metrics table, Valuation row | Replace "yield vs history," with "margin of safety against the" so it reads: "P/E, P/FCF, FCF yield, margin of safety against the Gordon-growth fair-value range (...)". Yield vs history stays in the Dividend record row only. |
| Dividend handling | Replace the "Price multiples use TTM figures..." bullet with: "Price multiples use TTM figures when four consecutive quarters of net income, operating cash flow and capex end after the latest fiscal year (EPS is then TTM net income over the latest quarter's diluted shares, because gold never derives a Q4 EPS) and the latest fiscal year otherwise. The basis is stored per company and shown in the UI." Add a bullet: "Per-share and share-count values from filings are restated to today's share basis with the splits whose ex-date is after the filing date." Add: "The streak counts back to the start of the available dividend history (about 1970 for old companies); the UI marks a streak that reaches it with a plus." |
| Universe | Add the SIC ranges: banks and credit 6000-6199, insurance 6300-6411, REITs 6798 (asset managers stay scored). |
| Refresh stage 1 | Replace "`content_hash`" with "the upstream key (`content_hash` plus `gold_built_at` from `/health`; the hash alone is the schema hash and does not change when data does)". Replace "the metrics code version" with "the metrics version (metrics code plus the `metrics:` section of `config/scoring.yaml`)". Replace "Staging tables are dropped." with "Staging happens in a working file; only the persisted tables are copied into the new cache." |
| Cache tables | Add `dividend_payment` and `price_yearend`; `meta` also holds `contract_warnings`; `fundamentals_annual` has `dividends_estimated`. |
| Layout | Add `src/dgi/schema.py` (shared table definitions) and `src/dgi/cache/{meta,build,checks,handle,screener,company,status}.py`, `src/dgi/web/{app,series,format}.py`. |
| Architecture | Add: "The company page's daily series come from a `PriceSource` that `pipeline` injects into the web app, so `client` stays imported by `pipeline` only." |
| Views, Screener | Add: "A company with an unknown value fails a filter limit that is set." |
| Views, Other | `/health` is always 200 (`{"status": "no_cache"}` before the first refresh) so readiness probes pass. |

- [ ] **Step 9: Update `CLAUDE.md` Status, verify isolation, stop the API, commit**

In `CLAUDE.md`, change the Status paragraph's first sentence to: "Implemented per the plan in `docs/superpowers/plans/` through the local acceptance (`docs/acceptance-local.md`); the Kubernetes deployment follows in Tasks 24-26." and change "Acceptance against the real local API is a separate, manual step (plan Task 24)." to "(plan Task 23)".

```bash
pkill -f "invest serve"
scripts/check_isolation.sh verify
git add docs CLAUDE.md
git commit -m "docs: local acceptance results and spec clarifications"
```
Expected: `OK: ../capital-trading and ../investment are unchanged.` (`../investment` may carry its owner's own uncommitted edits; the baseline from Task 1 recorded them, and `invest serve` writes nothing.)
