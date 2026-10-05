# Phase 3: Metrics (Tasks 7-10)

Part of `../2026-10-05-dgi-screener.md` (read its Global Constraints, Review Focus and "Spec clarifications" first). Spec: `../../specs/2026-10-05-dgi-screener-design.md`, section "Metrics and scoring".

The metrics layer turns the staged `stg_*` tables into the cache tables `company_dim`, `dividend_payment`, `dividend_annual`, `price_yearend`, `fundamentals_annual` and `metrics_current` (columns in `src/dgi/schema.py`). Transforms are SQL files in `src/dgi/metrics/sql/`; only streak and CAGR logic is Python, because it walks a per-ticker series. No SQL file splices a value into text: run-wide values (today, caps, tolerances) come from a one-row temp table `run_params`.

Conventions that matter in these tasks:

- Tickers are the key everywhere (statements carry the current primary ticker; rows without a ticker are dropped).
- Dividends: `amount_adj = amount / product(ratio of splits whose ex_date is after the payment's ex_date)`. A split on the payment's own ex-date does not apply (gold: the bar on a split's ex-date is already post-split).
- Per-share and share-count values from filings are restated with the splits whose ex_date is **after the filing date** (`filed`); splits before the filing are already inside the filed number.
- Payout ratios use dollars (dividends paid / net income, / FCF), capped at `payout_cap` (999%), and set to the cap when the denominator is not positive while dividends are paid. Some large filers report no cash dividends paid (JNJ, found when this design was run against the real local API); then the fiscal year's regular dividend per share times diluted shares stands in, and `dividends_estimated` records it. Coverage and leverage ratios are capped at `ratio_cap` (99).
- The SQL was checked against DuckDB 1.5 while writing this plan (LATERAL joins, `QUALIFY`, `INSERT ... BY NAME`, macros, multi-statement files). If a later DuckDB changes one of these, the tests below pin the behavior.

---

### Task 7: Metrics parameters, SQL runner, and split-adjusted dividend tables

**Files:**
- Create: `src/dgi/metrics/__init__.py` (replaced in Task 10), `src/dgi/metrics/params.py`, `src/dgi/metrics/sqlrun.py`, `src/dgi/metrics/sql/macros.sql`, `src/dgi/metrics/sql/dividend_payment.sql`, `src/dgi/metrics/sql/dividend_annual.sql`
- Test: `tests/metrics_fixtures.py` (shared by Tasks 7-10), `tests/test_metrics_dividend_tables.py`

**Interfaces:**
- Consumes: `dgi.schema` (`create_tables`, `recreate_tables`, tables `dividend_payment`, `dividend_annual`), staged tables `stg_dividends(ticker, ex_date, amount)` and `stg_splits(ticker, ex_date, ratio)`.
- Produces:
  - `MetricParams` (pydantic: `raise_tolerance=0.001`, `cut_tolerance=0.01`, `special_ratio=1.5`, `ttm_min_span_days=240`, `ttm_max_span_days=300`, `payout_cap=9.99`, `ratio_cap=99.0`) and `params_hash(params) -> str`.
  - `read_sql(name)`, `run_sql_file(con, name)`, `create_run_params(con, today: date, params: MetricParams) -> None` (creates temp table `run_params` and the temp macros `capped_ratio(num, den, cap)` and `cagr(first_value, last_value, years)`).
  - Tables: `dividend_payment(ticker, ex_date, year, amount_adj, is_special)` and `dividend_annual(ticker, year, dps, n_payments, special_total, complete)`.
  - Test helpers in `tests/metrics_fixtures.py`: `TODAY` (2026-10-05), `D` (= `datetime.date`), `metrics_con(today=TODAY, params=None)`, `add_company`, `add_dividends`, `quarterly`, `add_split`, `add_facts`, `add_quarters`, `add_prices`, `add_year_end_prices`.

- [ ] **Step 1: Write the failing tests and the shared fixtures**

`tests/metrics_fixtures.py`:
```python
"""Hand-built staging data for the metrics tests: no API, no network."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import duckdb

from dgi.metrics.params import MetricParams
from dgi.metrics.sqlrun import create_run_params
from dgi.schema import create_tables

TODAY = dt.date(2026, 10, 5)
D = dt.date

_STATEMENT = (
    "(ticker VARCHAR, cik BIGINT, concept VARCHAR, period_kind VARCHAR, period_end DATE, fiscal_year INTEGER, "
    "fiscal_period VARCHAR, value DOUBLE, filed DATE, data_flag VARCHAR)"
)
STAGING_DDL = {
    "stg_company": "(cik BIGINT, name VARCHAR, ticker VARCHAR, sic VARCHAR, sic_description VARCHAR)",
    "stg_dividends": "(ticker VARCHAR, cik BIGINT, ex_date DATE, amount DOUBLE)",
    "stg_splits": "(ticker VARCHAR, cik BIGINT, ex_date DATE, numerator DOUBLE, denominator DOUBLE, ratio DOUBLE)",
    "stg_income_annual": _STATEMENT,
    "stg_cash_annual": _STATEMENT,
    "stg_balance_annual": _STATEMENT,
    "stg_shares_cover": _STATEMENT,
    "stg_income_quarter": _STATEMENT,
    "stg_cash_quarter": _STATEMENT,
    "stg_prices_latest": "(ticker VARCHAR, cik BIGINT, trade_date DATE, close DOUBLE)",
    "stg_prices_yearend": "(ticker VARCHAR, cik BIGINT, trade_date DATE, close DOUBLE)",
}


def metrics_con(today: dt.date = TODAY, params: MetricParams | None = None) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with empty staging tables, every cache table, and the run parameters."""
    con = duckdb.connect()
    for name, columns in STAGING_DDL.items():
        con.execute(f"CREATE TABLE {name} {columns}")
    create_tables(con)
    create_run_params(con, today, params or MetricParams())
    return con


def add_company(con: duckdb.DuckDBPyConnection, ticker: str, cik: int = 1, sic: str = "2000", name: str | None = None) -> None:
    con.execute("INSERT INTO stg_company VALUES (?, ?, ?, ?, ?)", [cik, name or f"{ticker} Inc", ticker, sic, "x"])


def add_dividends(con: duckdb.DuckDBPyConnection, ticker: str, payments: Iterable[tuple[dt.date, float]]) -> None:
    con.executemany("INSERT INTO stg_dividends VALUES (?, 1, ?, ?)", [(ticker, d, a) for d, a in payments])


def quarterly(year: int, amount: float, months: tuple[int, ...] = (2, 5, 8, 11)) -> list[tuple[dt.date, float]]:
    return [(D(year, m, 10), amount) for m in months]


def add_split(con: duckdb.DuckDBPyConnection, ticker: str, ex_date: dt.date, ratio: float) -> None:
    con.execute("INSERT INTO stg_splits VALUES (?, 1, ?, ?, 1, ?)", [ticker, ex_date, ratio, ratio])


def add_facts(
    con: duckdb.DuckDBPyConnection,
    table: str,
    ticker: str,
    rows: Iterable[tuple[str, int, float]],
    *,
    kind: str = "annual",
    filed_lag_days: int = 60,
    data_flag: str | None = None,
    cik: int = 1,
) -> None:
    """rows are (concept, fiscal_year, value); the period ends 31 December of that year and is filed `filed_lag_days` later."""
    records = []
    for concept, fy, value in rows:
        end = D(fy, 12, 31)
        records.append((ticker, cik, concept, kind, end, fy, "FY", value, end + dt.timedelta(days=filed_lag_days), data_flag))
    con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records)


def add_quarters(
    con: duckdb.DuckDBPyConnection, table: str, ticker: str, concept: str, ends: Iterable[dt.date], value: float, *, filed_lag_days: int = 40
) -> None:
    con.executemany(
        f"INSERT INTO {table} VALUES (?, 1, ?, 'quarter', ?, ?, 'Q', ?, ?, NULL)",
        [(ticker, concept, e, e.year, value, e + dt.timedelta(days=filed_lag_days)) for e in ends],
    )


def add_prices(con: duckdb.DuckDBPyConnection, table: str, ticker: str, rows: Iterable[tuple[dt.date, float]]) -> None:
    con.executemany(f"INSERT INTO {table} VALUES (?, 1, ?, ?)", [(ticker, d, c) for d, c in rows])


def add_year_end_prices(con: duckdb.DuckDBPyConnection, ticker: str, closes: dict[int, float]) -> None:
    add_prices(con, "stg_prices_yearend", ticker, [(D(y, 12, 30), c) for y, c in closes.items()])
```

`tests/test_metrics_dividend_tables.py` (UNIT: dividend_payment.sql and dividend_annual.sql):
```python
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import D, add_dividends, add_split, metrics_con, quarterly


def build(con):
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")


def annual(con, ticker):
    rows = con.execute(
        "SELECT year, round(dps, 6), n_payments, round(special_total, 6), complete FROM dividend_annual WHERE ticker = ? ORDER BY year", [ticker]
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_a_split_restates_older_payments_so_it_creates_no_fake_cut():
    con = metrics_con()
    # 2-for-1 on 2022-06-01: payments before it were paid on the old share basis (1.00), after it 0.50
    add_dividends(con, "AAA", quarterly(2021, 1.0) + quarterly(2022, 1.0)[:2] + quarterly(2022, 0.5)[2:] + quarterly(2023, 0.5))
    add_split(con, "AAA", D(2022, 6, 1), 2.0)
    build(con)
    got = annual(con, "AAA")
    assert got[2021] == (2.0, 4, 0.0, True)
    assert got[2022] == (2.0, 4, 0.0, True)
    assert got[2023] == (2.0, 4, 0.0, True)


def test_a_reverse_split_scales_the_other_way():
    con = metrics_con()
    add_dividends(con, "AAA", quarterly(2021, 0.1) + quarterly(2023, 1.0))
    add_split(con, "AAA", D(2022, 1, 3), 0.1)  # 1-for-10 reverse split: ratio 0.1
    build(con)
    assert annual(con, "AAA")[2021][0] == 4.0 and annual(con, "AAA")[2023][0] == 4.0


def test_a_special_dividend_is_excluded_from_the_total_and_recorded():
    con = metrics_con()
    regular = [p for y in range(2019, 2024) for p in quarterly(y, 0.25)]
    add_dividends(con, "AAA", regular + [(D(2023, 12, 20), 3.0)])
    build(con)
    assert annual(con, "AAA")[2023] == (1.0, 4, 3.0, True)
    assert con.execute("SELECT ex_date FROM dividend_payment WHERE is_special").fetchall() == [(D(2023, 12, 20),)]


def test_an_extra_payment_below_the_special_ratio_stays_in_the_total():
    con = metrics_con()
    regular = [p for y in range(2019, 2024) for p in quarterly(y, 0.25)]
    add_dividends(con, "AAA", regular + [(D(2023, 12, 20), 0.30)])  # 0.30 < 1.5 x 0.25
    build(con)
    assert annual(con, "AAA")[2023] == (1.3, 5, 0.0, True)


def test_the_current_year_is_not_complete_and_future_dated_rows_are_ignored():
    con = metrics_con()  # today is 2026-10-05
    add_dividends(con, "AAA", [p for y in range(2023, 2026) for p in quarterly(y, 0.25)] + [(D(2026, 2, 10), 0.25), (D(2026, 11, 10), 0.25)])
    build(con)
    got = annual(con, "AAA")
    assert got[2026] == (0.25, 1, 0.0, False)
    assert got[2025][3] is True


def test_zero_and_negative_amounts_are_ignored():
    con = metrics_con()
    add_dividends(con, "AAA", quarterly(2024, 0.25) + [(D(2024, 12, 1), 0.0), (D(2024, 12, 2), -0.1)])
    build(con)
    assert annual(con, "AAA")[2024] == (1.0, 4, 0.0, True)


def test_payments_of_other_tickers_do_not_mix():
    con = metrics_con()
    add_dividends(con, "AAA", quarterly(2024, 0.25))
    add_dividends(con, "BBB", quarterly(2024, 1.0, months=(3, 9)))
    build(con)
    assert annual(con, "AAA")[2024][0] == 1.0 and annual(con, "BBB")[2024][0] == 2.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_metrics_dividend_tables.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.metrics'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/metrics/__init__.py` (a stub now so the package imports; Task 10 replaces it):
```python
"""Derived metrics: SQL over the staged API data plus the streak code that does not belong in SQL."""
```

`src/dgi/metrics/params.py`:
```python
"""Settings that change how history is read. They live in the `metrics:` section of config/scoring.yaml."""

from __future__ import annotations

import json

from pydantic import BaseModel

from dgi.fsutil import text_sha256


class MetricParams(BaseModel):
    raise_tolerance: float = 0.001   # a raise is more than this fraction above the prior year
    cut_tolerance: float = 0.01      # a cut is more than this fraction below the prior year
    special_ratio: float = 1.5       # an extra payment this many times the median regular one is special
    ttm_min_span_days: int = 240     # four quarters span 240-300 days between first and last period end
    ttm_max_span_days: int = 300
    payout_cap: float = 9.99         # payout ratios are capped at 999%
    ratio_cap: float = 99.0          # coverage and leverage ratios are capped here


def params_hash(params: MetricParams) -> str:
    return text_sha256(json.dumps(params.model_dump(), sort_keys=True))
```

`src/dgi/metrics/sqlrun.py`:
```python
"""Run the SQL files in metrics/sql against a DuckDB connection."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from dgi.metrics.params import MetricParams

SQL_DIR = Path(__file__).with_name("sql")


def read_sql(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text()


def run_sql_file(con: duckdb.DuckDBPyConnection, name: str) -> None:
    con.execute(read_sql(name))


def create_run_params(con: duckdb.DuckDBPyConnection, today: date, params: MetricParams) -> None:
    """One-row temp table the SQL files read (`SELECT today FROM run_params`), so no value is spliced into SQL text."""
    con.execute(
        "CREATE OR REPLACE TEMP TABLE run_params (today DATE, special_ratio DOUBLE, payout_cap DOUBLE, "
        "ratio_cap DOUBLE, ttm_min_span_days INTEGER, ttm_max_span_days INTEGER)"
    )
    con.execute(
        "INSERT INTO run_params VALUES (?, ?, ?, ?, ?, ?)",
        [today, params.special_ratio, params.payout_cap, params.ratio_cap, params.ttm_min_span_days, params.ttm_max_span_days],
    )
    run_sql_file(con, "macros")
```

`src/dgi/metrics/sql/macros.sql`:
```sql
CREATE OR REPLACE TEMP MACRO capped_ratio(num, den, cap) AS
    CASE WHEN num IS NULL OR den IS NULL THEN NULL
         WHEN num <= 0 THEN 0.0
         WHEN den > 0 THEN least(num / den, cap)
         ELSE cap END;
CREATE OR REPLACE TEMP MACRO cagr(first_value, last_value, years) AS
    CASE WHEN first_value > 0 AND last_value > 0 THEN pow(last_value / first_value, 1.0 / years) - 1 END;
```

`dividend_payment.sql` re-adjusts every payment to today's share basis, then marks specials. The "usual count" per ticker is the most common payments-per-year over complete years (ties go to the smaller count); in a year with more payments than usual, the largest payment is special if it is at least `special_ratio` times the median of that year's other payments.

`src/dgi/metrics/sql/dividend_payment.sql`:
```sql
INSERT INTO dividend_payment BY NAME
WITH adj AS (
    SELECT d.ticker, d.ex_date, year(d.ex_date) AS year,
           d.amount / COALESCE(f.factor, 1.0) AS amount_adj
    FROM stg_dividends d
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stg_splits s
        WHERE s.ticker = d.ticker AND s.ex_date > d.ex_date AND s.ratio > 0
    ) f ON TRUE
    WHERE d.ticker IS NOT NULL AND d.amount > 0 AND d.ex_date <= (SELECT today FROM run_params)
),
counts AS (
    SELECT ticker, year, count(*) AS n FROM adj GROUP BY ticker, year
),
usual AS (
    SELECT ticker, n AS usual_n
    FROM (
        SELECT ticker, n, count(*) AS freq
        FROM counts
        WHERE year < year((SELECT today FROM run_params))
        GROUP BY ticker, n
    )
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY freq DESC, n ASC) = 1
),
ranked AS (
    SELECT a.ticker, a.ex_date, a.year, a.amount_adj, c.n, u.usual_n,
           row_number() OVER (PARTITION BY a.ticker, a.year ORDER BY a.amount_adj DESC, a.ex_date) AS rk
    FROM adj a
    JOIN counts c ON c.ticker = a.ticker AND c.year = a.year
    LEFT JOIN usual u ON u.ticker = a.ticker
),
others AS (
    SELECT ticker, year, median(amount_adj) AS others_median
    FROM ranked WHERE rk > 1 GROUP BY ticker, year
)
SELECT r.ticker, r.ex_date, r.year, r.amount_adj,
       COALESCE(
           r.rk = 1 AND r.n > COALESCE(r.usual_n, r.n)
           AND r.amount_adj >= (SELECT special_ratio FROM run_params) * o.others_median,
           FALSE) AS is_special
FROM ranked r
LEFT JOIN others o ON o.ticker = r.ticker AND o.year = r.year;
```

`src/dgi/metrics/sql/dividend_annual.sql`:
```sql
INSERT INTO dividend_annual BY NAME
SELECT ticker, year,
       sum(amount_adj) FILTER (WHERE NOT is_special) AS dps,
       count(*) FILTER (WHERE NOT is_special) AS n_payments,
       COALESCE(sum(amount_adj) FILTER (WHERE is_special), 0.0) AS special_total,
       year < year((SELECT today FROM run_params)) AS complete
FROM dividend_payment
GROUP BY ticker, year
HAVING count(*) FILTER (WHERE NOT is_special) > 0;
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_metrics_dividend_tables.py`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/metrics tests/metrics_fixtures.py tests/test_metrics_dividend_tables.py
git commit -m "feat: split-adjusted dividend payments with special-dividend detection"
```

---

### Task 8: Streaks, growth rates and dividend metrics

Pure functions over one ticker's annual series, plus `build_dividend_metrics`, which reads `dividend_annual` (complete years only) and writes the temp table `tmp_dividend_metrics` that `metrics_current.sql` joins.

**Files:**
- Create: `src/dgi/metrics/dividends.py`
- Test: `tests/test_dividends.py`

**Interfaces:**
- Consumes: `dividend_annual` (Task 7).
- Produces:
  - `compute_streaks(series: Mapping[int, float], last_year: int, raise_tol: float, cut_tol: float) -> tuple[int, int]` returning `(increase_streak, no_cut_streak)`. Walking back from `last_year`: a year is a *raise* if it is more than `raise_tol` above the prior year, a *cut* if more than `cut_tol` below it, otherwise *flat*; a missing year (or missing prior year) ends both streaks. The walk stops at the first year of history, so a streak never exceeds the history.
  - `dividend_cagr(series, last_year, years) -> float | None`, `count_cut_years(series, last_year, window, raise_tol, cut_tol) -> int` (cuts and suspensions in the window), `usual_count(counts) -> int | None`.
  - `DividendMetrics` (frozen dataclass: `streak`, `no_cut_streak`, `dgr_1`, `dgr_3`, `dgr_5`, `dgr_10`, `years_history`, `payment_frequency`, `cut_years_5y`, `irregular_payments`), `compute_dividend_metrics(series, counts, last_year, raise_tol, cut_tol) -> DividendMetrics`.
  - `build_dividend_metrics(con, today: date, raise_tol: float, cut_tol: float) -> int` (tickers written to `tmp_dividend_metrics`; `last_year = today.year - 1`).

- [ ] **Step 1: Write the failing tests**

`tests/test_dividends.py` (UNIT: streak, CAGR and dividend metric functions; one SQL-plus-Python case with a split and a special dividend):
```python
import pytest

from dgi.metrics.dividends import (
    DividendMetrics,
    build_dividend_metrics,
    compute_dividend_metrics,
    compute_streaks,
    count_cut_years,
    dividend_cagr,
    usual_count,
)
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import D, TODAY, add_dividends, add_split, metrics_con, quarterly

RAISE, CUT = 0.001, 0.01


def streaks(series, last_year):
    return compute_streaks(series, last_year, RAISE, CUT)


def test_every_year_a_raise_counts_back_to_the_start_of_the_history():
    series = {2020: 1.0, 2021: 1.1, 2022: 1.2, 2023: 1.3}
    assert streaks(series, 2023) == (3, 3)  # three steps: 2021, 2022, 2023


def test_a_flat_year_ends_the_increase_streak_but_not_the_no_cut_streak():
    series = {2019: 1.0, 2020: 1.1, 2021: 1.1, 2022: 1.2, 2023: 1.3}
    assert streaks(series, 2023) == (2, 4)


def test_a_change_inside_the_raise_tolerance_counts_as_flat():
    series = {2022: 1.0, 2023: 1.0005}  # +0.05% is under the 0.1% raise tolerance
    assert streaks(series, 2023) == (0, 1)


def test_a_cut_ends_both_streaks():
    series = {2019: 1.0, 2020: 1.1, 2021: 0.9, 2022: 1.0, 2023: 1.1}
    assert streaks(series, 2023) == (2, 2)


def test_a_small_dip_inside_the_cut_tolerance_is_flat_not_a_cut():
    series = {2022: 1.00, 2023: 0.995}  # -0.5% is inside the 1% cut tolerance
    assert streaks(series, 2023) == (0, 1)


def test_a_company_that_stopped_paying_has_no_streak_and_a_cut():
    series = {2018: 1.0, 2019: 1.1, 2020: 1.2, 2021: 1.3}  # nothing in 2022-2025
    assert streaks(series, 2025) == (0, 0)
    assert count_cut_years(series, 2025, 5, RAISE, CUT) >= 1


def test_a_gap_year_inside_the_history_stops_the_streak_there():
    series = {2019: 1.0, 2020: 1.1, 2022: 1.3, 2023: 1.4}  # 2021 missing
    assert streaks(series, 2023) == (1, 1)  # only 2023 vs 2022; 2022 vs 2021 has no prior year


def test_the_streak_never_exceeds_the_available_history():
    series = {2023: 1.0, 2024: 1.1}
    inc, no_cut = streaks(series, 2024)
    assert inc == 1 and no_cut == 1


def test_an_empty_series_has_no_streak():
    assert streaks({}, 2025) == (0, 0)


def test_dividend_cagr():
    series = {2020: 1.0, 2025: 2.0}
    assert dividend_cagr(series, 2025, 5) == pytest.approx(2 ** (1 / 5) - 1)
    assert dividend_cagr(series, 2025, 3) is None  # 2022 missing
    assert dividend_cagr({2024: 0.0, 2025: 1.0}, 2025, 1) is None  # cannot grow from zero


def test_cut_years_counts_cuts_and_suspensions_inside_the_window():
    series = {2019: 1.0, 2020: 0.5, 2021: 0.6, 2022: 0.7, 2023: 0.8, 2024: 0.9}
    assert count_cut_years(series, 2024, 5, RAISE, CUT) == 1  # 2020 is inside 2020-2024
    assert count_cut_years(series, 2024, 3, RAISE, CUT) == 0


def test_usual_count_is_the_most_common_with_ties_going_to_the_smaller():
    assert usual_count({2020: 4, 2021: 4, 2022: 5}) == 4
    assert usual_count({2020: 2, 2021: 4}) == 2
    assert usual_count({}) is None


def test_compute_dividend_metrics_collects_everything():
    series = {y: 1.0 * 1.05 ** (y - 2014) for y in range(2014, 2026)}
    counts = {y: 4 for y in series}
    counts[2025] = 5
    m = compute_dividend_metrics(series, counts, 2025, RAISE, CUT)
    assert (m.streak, m.no_cut_streak, m.years_history, m.cut_years_5y) == (11, 11, 12, 0)
    assert m.dgr_5 == pytest.approx(0.05) and m.dgr_10 == pytest.approx(0.05)
    assert m.payment_frequency == 5 and m.irregular_payments is True


def test_a_split_and_a_special_dividend_do_not_disturb_the_streak():
    con = metrics_con()
    payments = []
    for year in range(2018, 2026):
        amount = 0.25 * 1.06 ** (year - 2018)
        payments += quarterly(year, amount * (2.0 if year < 2022 else 1.0))  # paid on the pre-split basis before 2022
    payments.append((D(2023, 12, 20), 5.0))  # special
    add_dividends(con, "AAA", payments)
    add_split(con, "AAA", D(2022, 1, 3), 2.0)
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    assert build_dividend_metrics(con, TODAY, RAISE, CUT) == 1
    row = con.execute("SELECT streak, no_cut_streak, years_history, cut_years_5y, irregular_payments FROM tmp_dividend_metrics").fetchone()
    assert row == (7, 7, 8, 0, False)


def test_the_partial_current_year_is_left_out_of_the_series():
    con = metrics_con()
    add_dividends(con, "AAA", [p for y in range(2022, 2026) for p in quarterly(y, 0.25 * 1.1 ** (y - 2022))] + [(D(2026, 2, 10), 0.01)])
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    build_dividend_metrics(con, TODAY, RAISE, CUT)
    assert con.execute("SELECT streak, years_history FROM tmp_dividend_metrics").fetchone() == (3, 4)


def test_build_dividend_metrics_with_no_payers_creates_an_empty_table():
    con = metrics_con()
    assert build_dividend_metrics(con, TODAY, RAISE, CUT) == 0
    assert con.execute("SELECT count(*) FROM tmp_dividend_metrics").fetchone() == (0,)


def test_metrics_dataclass_is_frozen():
    m = DividendMetrics(1, 1, None, None, None, None, 2, 4, 0, False)
    with pytest.raises(Exception):
        m.streak = 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_dividends.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.metrics.dividends'`.

- [ ] **Step 3: Write `src/dgi/metrics/dividends.py`**

`src/dgi/metrics/dividends.py`:
```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from itertools import groupby

import duckdb
import pyarrow as pa


@dataclass(frozen=True)
class DividendMetrics:
    streak: int
    no_cut_streak: int
    dgr_1: float | None
    dgr_3: float | None
    dgr_5: float | None
    dgr_10: float | None
    years_history: int
    payment_frequency: int | None
    cut_years_5y: int
    irregular_payments: bool


def _step(series: Mapping[int, float], year: int, raise_tol: float, cut_tol: float) -> str:
    cur, prev = series.get(year), series.get(year - 1)
    if cur is None or prev is None:
        return "gap"
    if cur > prev * (1 + raise_tol):
        return "raise"
    if cur < prev * (1 - cut_tol):
        return "cut"
    return "flat"


def compute_streaks(series: Mapping[int, float], last_year: int, raise_tol: float, cut_tol: float) -> tuple[int, int]:
    if not series:
        return 0, 0
    increases = no_cuts = 0
    increasing = not_cutting = True
    for year in range(last_year, min(series), -1):
        kind = _step(series, year, raise_tol, cut_tol)
        increasing = increasing and kind == "raise"
        not_cutting = not_cutting and kind in ("raise", "flat")
        if not (increasing or not_cutting):
            break
        increases += increasing
        no_cuts += not_cutting
    return increases, no_cuts


def dividend_cagr(series: Mapping[int, float], last_year: int, years: int) -> float | None:
    first, last = series.get(last_year - years), series.get(last_year)
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def count_cut_years(series: Mapping[int, float], last_year: int, window: int, raise_tol: float, cut_tol: float) -> int:
    cuts = 0
    for year in range(last_year - window + 1, last_year + 1):
        kind = _step(series, year, raise_tol, cut_tol)
        suspended = kind == "gap" and series.get(year) is None and series.get(year - 1) is not None
        cuts += kind == "cut" or suspended
    return cuts


def usual_count(counts: Mapping[int, int]) -> int | None:
    if not counts:
        return None
    tally: dict[int, int] = {}
    for n in counts.values():
        tally[n] = tally.get(n, 0) + 1
    return min(tally, key=lambda n: (-tally[n], n))


def compute_dividend_metrics(
    series: Mapping[int, float], counts: Mapping[int, int], last_year: int, raise_tol: float, cut_tol: float
) -> DividendMetrics:
    streak, no_cut = compute_streaks(series, last_year, raise_tol, cut_tol)
    last_count = counts.get(last_year)
    return DividendMetrics(
        streak=streak,
        no_cut_streak=no_cut,
        dgr_1=dividend_cagr(series, last_year, 1),
        dgr_3=dividend_cagr(series, last_year, 3),
        dgr_5=dividend_cagr(series, last_year, 5),
        dgr_10=dividend_cagr(series, last_year, 10),
        years_history=len(series),
        payment_frequency=last_count,
        cut_years_5y=count_cut_years(series, last_year, 5, raise_tol, cut_tol),
        irregular_payments=last_count is not None and last_count != usual_count(counts),
    )


METRICS_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("streak", pa.int32()), ("no_cut_streak", pa.int32()),
    ("dgr_1", pa.float64()), ("dgr_3", pa.float64()), ("dgr_5", pa.float64()), ("dgr_10", pa.float64()),
    ("years_history", pa.int32()), ("payment_frequency", pa.int32()), ("cut_years_5y", pa.int32()),
    ("irregular_payments", pa.bool_()),
])


def build_dividend_metrics(con: duckdb.DuckDBPyConnection, today: date, raise_tol: float, cut_tol: float) -> int:
    rows = con.execute(
        "SELECT ticker, year, dps, n_payments FROM dividend_annual WHERE complete ORDER BY ticker, year"
    ).fetchall()
    last_year = today.year - 1
    records: list[dict] = []
    for ticker, group in groupby(rows, key=lambda r: r[0]):
        group = list(group)
        series = {year: dps for _, year, dps, _ in group}
        counts = {year: n for _, year, _, n in group}
        metrics = compute_dividend_metrics(series, counts, last_year, raise_tol, cut_tol)
        records.append({"ticker": ticker, **metrics.__dict__})
    table = pa.Table.from_pylist(records, schema=METRICS_SCHEMA)
    con.register("dividend_metric_rows", table)
    con.execute("CREATE OR REPLACE TEMP TABLE tmp_dividend_metrics AS SELECT * FROM dividend_metric_rows")
    con.unregister("dividend_metric_rows")
    return len(records)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_dividends.py`
Expected: PASS (17 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/metrics/dividends.py tests/test_dividends.py
git commit -m "feat: dividend streaks, growth rates and per-ticker dividend metrics"
```

---

### Task 9: Annual fundamentals

One row per ticker and fiscal year with the statement concepts pivoted wide, per-share values restated to today's share basis, and the derived ratios the scoring needs.

**Files:**
- Create: `src/dgi/metrics/sql/fundamentals_annual.sql`
- Test: `tests/test_metrics_fundamentals.py`

**Interfaces:**
- Consumes: `stg_income_annual`, `stg_cash_annual`, `stg_balance_annual`, `stg_splits`, `dividend_annual` (Task 7); macro `capped_ratio` and `run_params.payout_cap` (Task 7).
- Produces: table `fundamentals_annual` with the columns in `schema.COLUMN_DEFS["fundamentals_annual"]`. `capex` and `dividends_paid` are absolute values; `fcf = cfo - capex` (NULL when either is missing); `debt` is the sum of the three debt concepts when the balance sheet was reported at all (a balance sheet with no debt tags means no debt) and NULL otherwise; `net_debt = debt - cash - short_term_investments` only when `debt` and `cash` are known; `eps_diluted` and `shares_diluted` are on today's share basis; `dividends_paid` is the reported value, or the estimate above when the filer reports none (`dividends_estimated` is true then; the estimate needs a complete year in `dividend_annual` and a share count).

- [ ] **Step 1: Write the failing tests**

`tests/test_metrics_fundamentals.py` (UNIT: fundamentals_annual.sql):
```python
import pytest

from dgi import schema
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import D, add_facts, add_split, metrics_con


def build(con):
    run_sql_file(con, "fundamentals_annual")


def row(con, ticker="AAA", fy=2024):
    cur = con.execute("SELECT * FROM fundamentals_annual WHERE ticker = ? AND fiscal_year = ?", [ticker, fy])
    names = [d[0] for d in cur.description]
    values = cur.fetchone()
    return dict(zip(names, values)) if values else None


def test_statements_are_pivoted_and_ratios_derived():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("revenue", 2024, 1000.0), ("net_income", 2024, 100.0), ("operating_income", 2024, 150.0), ("interest_expense", 2024, 10.0), ("shares_diluted_weighted", 2024, 50.0), ("eps_diluted", 2024, 2.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("cash_from_operations", 2024, 140.0), ("capex", 2024, -40.0), ("dividends_paid", 2024, -60.0), ("depreciation_amortization", 2024, 30.0)])
    add_facts(con, "stg_balance_annual", "AAA", [("total_equity", 2024, 400.0), ("current_assets", 2024, 300.0), ("current_liabilities", 2024, 150.0), ("cash_and_equivalents", 2024, 50.0), ("short_term_investments", 2024, 10.0), ("long_term_debt", 2024, 200.0), ("short_term_debt", 2024, 20.0), ("current_portion_long_term_debt", 2024, 30.0)])
    build(con)
    r = row(con)
    assert (r["revenue"], r["net_income"], r["capex"], r["dividends_paid"]) == (1000.0, 100.0, 40.0, 60.0)
    assert r["fcf"] == 100.0 and r["fcf_per_share"] == 2.0
    assert r["debt"] == 250.0 and r["net_debt"] == 190.0 and r["ebitda"] == 180.0
    assert r["op_margin"] == pytest.approx(0.15)
    assert r["payout_earnings"] == pytest.approx(0.6) and r["payout_fcf"] == pytest.approx(0.6)
    assert r["period_end"] == D(2024, 12, 31)


def test_flagged_rows_are_ignored():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)])
    add_facts(con, "stg_income_annual", "AAA", [("revenue", 2024, 9e9)], data_flag="period_end_after_filed")
    build(con)
    assert row(con)["revenue"] is None and row(con)["net_income"] == 100.0


def test_the_latest_filed_value_wins_for_one_concept_and_year():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)], filed_lag_days=60)
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 90.0)], filed_lag_days=400)  # a later restatement
    build(con)
    assert row(con)["net_income"] == 90.0


def test_rows_without_a_ticker_are_dropped():
    con = metrics_con()
    con.execute("INSERT INTO stg_income_annual VALUES (NULL, 9, 'net_income', 'annual', DATE '2024-12-31', 2024, 'FY', 5.0, DATE '2025-02-20', NULL)")
    build(con)
    assert con.execute("SELECT count(*) FROM fundamentals_annual").fetchone() == (0,)


def test_eps_and_shares_are_restated_for_splits_after_the_filing_only():
    con = metrics_con()
    # FY2021 filed 2022-03-01. A 2-for-1 split on 2022-06-01 is after the filing: restate. A split on 2021-06-01 is before: already in the filing.
    add_facts(con, "stg_income_annual", "AAA", [("eps_diluted", 2021, 4.0), ("shares_diluted_weighted", 2021, 50.0)], filed_lag_days=60)
    add_split(con, "AAA", D(2022, 6, 1), 2.0)
    add_split(con, "AAA", D(2021, 6, 1), 3.0)
    build(con)
    r = row(con, fy=2021)
    assert r["eps_diluted"] == pytest.approx(2.0) and r["shares_diluted"] == pytest.approx(100.0)


def test_payout_is_capped_not_null_when_earnings_are_not_positive_and_dividends_are_paid():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, -50.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("cash_from_operations", 2024, 10.0), ("capex", 2024, 30.0), ("dividends_paid", 2024, -20.0)])
    build(con)
    r = row(con)
    assert r["payout_earnings"] == 9.99 and r["payout_fcf"] == 9.99 and r["fcf"] == -20.0


def test_payout_is_zero_without_dividends_and_null_when_dividends_are_unknown():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("dividends_paid", 2024, 0.0)])
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2024, 100.0)], cik=2)
    build(con)
    assert row(con, "AAA")["payout_earnings"] == 0.0
    assert row(con, "BBB")["payout_earnings"] is None


def test_a_balance_sheet_without_debt_tags_means_no_debt_but_no_balance_sheet_means_unknown():
    con = metrics_con()
    add_facts(con, "stg_balance_annual", "AAA", [("total_equity", 2024, 100.0), ("cash_and_equivalents", 2024, 40.0)])
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2024, 1.0)], cik=2)
    build(con)
    assert row(con, "AAA")["debt"] == 0.0 and row(con, "AAA")["net_debt"] == -40.0
    assert row(con, "BBB")["debt"] is None and row(con, "BBB")["net_debt"] is None


def test_fcf_is_unknown_when_capex_is_missing():
    con = metrics_con()
    add_facts(con, "stg_cash_annual", "AAA", [("cash_from_operations", 2024, 100.0)])
    build(con)
    assert row(con)["fcf"] is None


def test_each_fiscal_year_is_its_own_row():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2023, 80.0), ("net_income", 2024, 100.0)])
    build(con)
    assert con.execute("SELECT fiscal_year, net_income FROM fundamentals_annual ORDER BY 1").fetchall() == [(2023, 80.0), (2024, 100.0)]


def add_regular_dividends(con, ticker, year, dps, complete=True):
    schema.insert_rows(con, "dividend_annual", [{"ticker": ticker, "year": year, "dps": dps, "n_payments": 4, "special_total": 0.0, "complete": complete}])


def test_reported_dividends_paid_win_over_the_per_share_estimate():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("dividends_paid", 2024, -60.0)])
    add_regular_dividends(con, "AAA", 2024, 9.0)
    build(con)
    r = row(con)
    assert r["dividends_paid"] == 60.0 and r["dividends_estimated"] is False and r["payout_earnings"] == pytest.approx(0.6)


def test_missing_dividends_paid_is_estimated_from_dividend_per_share_and_shares():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)])
    add_regular_dividends(con, "AAA", 2024, 1.2)
    build(con)
    r = row(con)
    assert r["dividends_paid"] == pytest.approx(60.0) and r["dividends_estimated"] is True and r["payout_earnings"] == pytest.approx(0.6)


def test_no_estimate_without_shares_without_dividends_or_from_an_incomplete_year():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)])                                      # no shares
    add_regular_dividends(con, "AAA", 2024, 1.2)
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)], cik=2)  # no dividends
    add_facts(con, "stg_income_annual", "CCC", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)], cik=3)
    add_regular_dividends(con, "CCC", 2024, 1.2, complete=False)                                                    # year not complete
    build(con)
    for ticker in ("AAA", "BBB", "CCC"):
        r = row(con, ticker)
        assert r["dividends_paid"] is None and r["dividends_estimated"] is False and r["payout_earnings"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_metrics_fundamentals.py`
Expected: FAIL with `FileNotFoundError` for `fundamentals_annual.sql` (the SQL file does not exist yet).

- [ ] **Step 3: Write the SQL**

`src/dgi/metrics/sql/fundamentals_annual.sql`:
```sql
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_metrics_fundamentals.py`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/metrics/sql/fundamentals_annual.sql tests/test_metrics_fundamentals.py
git commit -m "feat: annual fundamentals with split-restated per-share values and capped payouts"
```

---

### Task 10: Current metrics and `build_metrics`

Combines prices, yield history, fundamentals windows and the TTM basis into `metrics_current` (one row per company, `NULL` for what cannot be computed) and exposes `build_metrics`, the single call the pipeline makes for stage 1's metrics step.

**Files:**
- Create: `src/dgi/metrics/sql/company_dim.sql`, `price_yearend.sql`, `price_dividend.sql`, `fund_metrics.sql`, `ttm.sql`, `shares_out.sql`, `metrics_current.sql`; replace `src/dgi/metrics/__init__.py`
- Test: `tests/test_metrics_current.py`

**Interfaces:**
- Consumes: Tasks 7-9 outputs; staged `stg_company`, `stg_prices_latest`, `stg_prices_yearend`, `stg_shares_cover`, `stg_income_quarter`, `stg_cash_quarter`.
- Produces:
  - `build_metrics(con, today: date, params: MetricParams) -> MetricsResult(companies: int, dividend_payments: int, with_streak: int)`; it recreates `METRIC_TABLES` (`company_dim`, `dividend_payment`, `dividend_annual`, `price_yearend`, `fundamentals_annual`, `metrics_current`) so it can run twice on one connection.
  - `metrics_version(params: MetricParams) -> str` (`"<METRICS_VERSION>:<12 hex of params hash>"`), `METRICS_VERSION = "1"`.
  - Table `metrics_current` (columns in the schema). `basis` is `'ttm'` when four consecutive quarters of net income, operating cash flow and capex exist, end after the latest fiscal year and span 240-300 days, otherwise `'fy'` (NULL when there are no fundamentals); with `ttm`, EPS is TTM net income divided by the latest quarter's diluted shares (restated for splits after its filing). `pe`, `p_fcf` are NULL when the denominator is not positive. Market cap is the latest price times the latest cover-page share count (`shares_outstanding_cover`, else `shares_outstanding`).

- [ ] **Step 1: Write the failing tests**

`tests/test_metrics_current.py` (UNIT: price_yearend, price_dividend, fund_metrics, ttm, shares_out, metrics_current and build_metrics):
```python
import pytest

from dgi.metrics import build_metrics, metrics_version
from dgi.metrics.dividends import build_dividend_metrics
from dgi.metrics.params import MetricParams
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import (
    D, TODAY, add_company, add_dividends, add_facts, add_prices, add_quarters, add_split, add_year_end_prices, metrics_con, quarterly,
)


def first(con, sql, *args):
    return con.execute(sql, list(args)).fetchone()


def test_year_end_close_is_restated_for_splits_after_it_and_the_last_trading_day_wins():
    con = metrics_con()
    add_prices(con, "stg_prices_yearend", "AAA", [(D(2021, 12, 29), 90.0), (D(2021, 12, 30), 100.0), (D(2023, 12, 29), 60.0)])
    add_split(con, "AAA", D(2022, 6, 1), 2.0)
    run_sql_file(con, "price_yearend")
    assert con.execute("SELECT year, close_adj FROM price_yearend ORDER BY year").fetchall() == [(2021, 50.0), (2023, 60.0)]


def test_latest_price_is_the_most_recent_bar_per_ticker():
    con = metrics_con()
    add_prices(con, "stg_prices_latest", "AAA", [(D(2026, 10, 1), 59.0), (D(2026, 10, 2), 60.0)])
    run_sql_file(con, "price_dividend")
    assert first(con, "SELECT price, price_date FROM tmp_price") == (60.0, D(2026, 10, 2))


def test_dividend_ttm_counts_regular_payments_in_the_last_365_days_and_specials_over_five_years():
    con = metrics_con()  # today 2026-10-05: window starts 2025-10-05
    add_dividends(con, "AAA", [p for y in range(2019, 2026) for p in quarterly(y, 0.25)] + [(D(2026, 2, 10), 0.26), (D(2026, 5, 10), 0.26), (D(2026, 8, 10), 0.26), (D(2024, 12, 20), 3.0)])
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    run_sql_file(con, "price_dividend")
    ttm, specials = first(con, "SELECT dividend_ttm, special_count_5y FROM tmp_dividend_ttm")
    assert ttm == pytest.approx(0.25 + 0.26 * 3)  # 2025-11-10 plus three 2026 payments
    assert specials == 1


def test_average_yield_needs_three_complete_years_with_prices_and_the_suspect_count_flags_impossible_dividends():
    con = metrics_con()
    add_dividends(con, "AAA", [p for y in range(2020, 2026) for p in quarterly(y, 0.5)])
    add_year_end_prices(con, "AAA", {2020: 50.0, 2021: 50.0, 2022: 50.0, 2023: 50.0, 2024: 50.0, 2025: 50.0})
    add_dividends(con, "BBB", [p for y in range(2020, 2026) for p in quarterly(y, 60.0)])  # quarterly dividend above the share price
    add_year_end_prices(con, "BBB", {2020: 50.0, 2021: 50.0, 2022: 50.0, 2023: 50.0, 2024: 50.0, 2025: 50.0})
    add_dividends(con, "CCC", [p for y in range(2024, 2026) for p in quarterly(y, 0.5)])
    add_year_end_prices(con, "CCC", {2023: 50.0, 2024: 50.0, 2025: 50.0})
    run_sql_file(con, "price_yearend")
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    run_sql_file(con, "price_dividend")
    assert first(con, "SELECT div_yield_avg_5y FROM tmp_yield_avg WHERE ticker = 'AAA'")[0] == pytest.approx(2.0 / 50.0)
    assert first(con, "SELECT count(*) FROM tmp_yield_avg WHERE ticker = 'CCC'") == (0,)  # only two complete years
    assert first(con, "SELECT suspect_dividend_count FROM tmp_suspect WHERE ticker = 'BBB'")[0] == 20  # 2021-2025, four payments each
    assert first(con, "SELECT count(*) FROM tmp_suspect WHERE ticker = 'AAA'") == (0,)


def fund_facts(con, ticker="AAA", years=range(2015, 2026), cik=1):
    """Eleven fiscal years of a steady grower: revenue +4%, net income +5%, shares -1% a year."""
    for fy in years:
        n = fy - 2015
        add_facts(con, "stg_income_annual", ticker, [
            ("revenue", fy, 1000.0 * 1.04 ** n), ("net_income", fy, 100.0 * 1.05 ** n), ("operating_income", fy, 150.0 * 1.05 ** n),
            ("interest_expense", fy, 10.0), ("shares_diluted_weighted", fy, 100.0 * 0.99 ** n), ("eps_diluted", fy, 100.0 * 1.05 ** n / (100.0 * 0.99 ** n)),
        ], cik=cik)
        add_facts(con, "stg_cash_annual", ticker, [
            ("cash_from_operations", fy, 120.0 * 1.05 ** n), ("capex", fy, -20.0), ("dividends_paid", fy, -50.0 * 1.05 ** n), ("depreciation_amortization", fy, 30.0),
        ], cik=cik)
        add_facts(con, "stg_balance_annual", ticker, [
            ("total_equity", fy, 500.0), ("current_assets", fy, 300.0), ("current_liabilities", fy, 150.0), ("cash_and_equivalents", fy, 50.0), ("long_term_debt", fy, 200.0),
        ], cik=cik)


def test_fund_metrics_summarize_the_latest_year_and_the_windows():
    con = metrics_con()
    fund_facts(con)
    run_sql_file(con, "fundamentals_annual")
    run_sql_file(con, "fund_metrics")
    cur = con.execute("SELECT * FROM tmp_fund_metrics")
    r = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    assert r["latest_fy"] == 2025 and r["fy_period_end"] == D(2025, 12, 31)
    assert r["payout_earnings"] == pytest.approx(0.5) and r["payout_earnings_5y"] == pytest.approx(0.5)
    assert r["payout_fcf"] == pytest.approx(50 * 1.05 ** 10 / (120 * 1.05 ** 10 - 20))
    assert r["interest_coverage"] == pytest.approx(150 * 1.05 ** 10 / 10)
    assert r["net_debt_ebitda"] == pytest.approx(150 / (150 * 1.05 ** 10 + 30))
    assert r["current_ratio"] == 2.0 and r["positive_earnings_years"] == 10
    assert r["rev_cagr_5"] == pytest.approx(0.04) and r["eps_cagr_5"] == pytest.approx(1.05 / 0.99 - 1)
    assert r["share_trend_5"] == pytest.approx(-0.01) and r["roe"] == pytest.approx(100 * 1.05 ** 10 / 500)
    assert r["op_margin_std"] is not None


def test_fund_metrics_edge_cases_cap_or_leave_null():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2025, 10.0), ("operating_income", 2025, 5.0)])  # no interest expense reported
    add_facts(con, "stg_balance_annual", "AAA", [("total_equity", 2025, -100.0), ("cash_and_equivalents", 2025, 1.0), ("long_term_debt", 2025, 500.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("depreciation_amortization", 2025, -10.0)])  # EBITDA = 5 - 10 < 0
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2025, 10.0)], cik=2)
    add_facts(con, "stg_balance_annual", "BBB", [("cash_and_equivalents", 2025, 900.0)], cik=2)
    add_facts(con, "stg_income_annual", "BBB", [("operating_income", 2025, 100.0)], cik=2)
    add_facts(con, "stg_cash_annual", "BBB", [("depreciation_amortization", 2025, 20.0)], cik=2)
    run_sql_file(con, "fundamentals_annual")
    run_sql_file(con, "fund_metrics")
    a = first(con, "SELECT interest_coverage, net_debt_ebitda, roe FROM tmp_fund_metrics WHERE ticker = 'AAA'")
    assert a == (99.0, 99.0, None)  # no interest -> capped coverage; positive net debt on negative EBITDA -> capped; negative equity -> no ROE
    b = first(con, "SELECT net_debt_ebitda FROM tmp_fund_metrics WHERE ticker = 'BBB'")
    assert b == (0.0,)  # net cash


def ttm_quarters(con, ticker, ends, ni=30.0, cfo=35.0, capex=-6.0, shares=90.0):
    add_quarters(con, "stg_income_quarter", ticker, "net_income", ends, ni)
    add_quarters(con, "stg_income_quarter", ticker, "shares_diluted_weighted", ends, shares)
    add_quarters(con, "stg_cash_quarter", ticker, "cash_from_operations", ends, cfo)
    add_quarters(con, "stg_cash_quarter", ticker, "capex", ends, capex)


FOUR = [D(2024, 12, 31), D(2025, 3, 31), D(2025, 6, 30), D(2025, 9, 30)]


def test_ttm_uses_four_consecutive_quarters_of_all_three_concepts():
    con = metrics_con()
    ttm_quarters(con, "AAA", FOUR)
    run_sql_file(con, "ttm")
    assert first(con, "SELECT ttm_end, ni_ttm, cfo_ttm, capex_ttm, shares_ttm FROM tmp_ttm") == (D(2025, 9, 30), 120.0, 140.0, 24.0, 90.0)


def test_ttm_is_absent_when_a_quarter_is_missing_the_span_is_wrong_or_a_concept_ends_earlier():
    con = metrics_con()
    ttm_quarters(con, "AAA", FOUR[:3] + [D(2023, 9, 30)])          # fourth quarter a year earlier: span too long
    ttm_quarters(con, "BBB", FOUR[:3])                              # only three quarters
    ttm_quarters(con, "CCC", FOUR)
    con.execute("DELETE FROM stg_cash_quarter WHERE ticker = 'CCC' AND period_end = DATE '2025-09-30'")  # cash flow ends a quarter earlier
    run_sql_file(con, "ttm")
    assert first(con, "SELECT count(*) FROM tmp_ttm") == (0,)


def test_ttm_shares_are_restated_for_splits_after_the_filing():
    con = metrics_con()
    ttm_quarters(con, "AAA", FOUR)
    add_split(con, "AAA", D(2026, 1, 5), 2.0)  # after the latest filing (2025-11-09)
    run_sql_file(con, "ttm")
    assert first(con, "SELECT shares_ttm FROM tmp_ttm") == (180.0,)


def test_shares_out_prefers_the_cover_count_over_the_balance_sheet_count_on_the_same_date():
    con = metrics_con()
    add_quarters(con, "stg_shares_cover", "AAA", "shares_outstanding", [D(2025, 9, 30)], 80.0)
    add_quarters(con, "stg_shares_cover", "AAA", "shares_outstanding_cover", [D(2025, 9, 30)], 88.0)
    run_sql_file(con, "shares_out")
    assert first(con, "SELECT shares_out FROM tmp_shares_out") == (88.0,)


def staged_grower(con, ticker="AAA", with_quarters=False):
    add_company(con, ticker)
    add_dividends(con, ticker, [p for y in range(2014, 2026) for p in quarterly(y, 0.25 * 1.05 ** (y - 2014))] + quarterly(2026, 0.25 * 1.05 ** 12)[:3])
    fund_facts(con, ticker)
    add_year_end_prices(con, ticker, {y: 20.0 * 1.1 ** (y - 2014) for y in range(2014, 2026)})
    add_prices(con, "stg_prices_latest", ticker, [(D(2026, 10, 2), 60.0)])
    add_quarters(con, "stg_shares_cover", ticker, "shares_outstanding_cover", [D(2025, 12, 31)], 88.0)
    if with_quarters:
        ttm_quarters(con, ticker, [D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30), D(2026, 9, 30)], ni=40.0, shares=88.0)


def test_build_metrics_end_to_end_for_a_payer_and_a_company_without_dividends():
    con = metrics_con()
    staged_grower(con)
    add_company(con, "NEW", cik=2)
    result = build_metrics(con, TODAY, MetricParams())
    assert (result.companies, result.with_streak) == (2, 1) and result.dividend_payments > 40
    cur = con.execute("SELECT * FROM metrics_current WHERE ticker = 'AAA'")
    m = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    assert (m["streak"], m["no_cut_streak"], m["years_history"], m["payment_frequency"]) == (11, 11, 12, 4)
    assert m["dgr_5"] == pytest.approx(0.05) and m["dgr_10"] == pytest.approx(0.05)
    assert m["price"] == 60.0 and m["market_cap"] == 60.0 * 88.0
    assert m["dividend_ttm"] == pytest.approx(0.25 * 1.05 ** 11 + 0.25 * 1.05 ** 12 * 3)  # Nov 2025 plus three 2026 payments
    assert m["basis"] == "fy" and m["latest_fy"] == 2025
    assert m["pe"] == pytest.approx(60.0 / (100 * 1.05 ** 10 / (100 * 0.99 ** 10)))
    assert m["div_yield_avg_5y"] is not None and m["yield_vs_avg"] is not None
    assert m["cut_years_5y"] == 0 and m["special_count_5y"] == 0 and m["suspect_dividend_count"] == 0
    n = con.execute("SELECT streak, price, latest_fy, special_count_5y, suspect_dividend_count FROM metrics_current WHERE ticker = 'NEW'").fetchone()
    assert n == (None, None, None, 0, 0)


def test_build_metrics_switches_to_the_ttm_basis_when_quarters_run_past_the_fiscal_year():
    con = metrics_con()
    staged_grower(con, with_quarters=True)
    build_metrics(con, TODAY, MetricParams())
    m = con.execute("SELECT basis, eps_basis, fcf_ps_basis FROM metrics_current WHERE ticker = 'AAA'").fetchone()
    assert m[0] == "ttm" and m[1] == pytest.approx(160.0 / 88.0)
    assert m[2] == pytest.approx((140.0 - 24.0) / 88.0)


def test_build_metrics_can_run_twice_on_the_same_connection():
    con = metrics_con()
    staged_grower(con)
    build_metrics(con, TODAY, MetricParams())
    again = build_metrics(con, TODAY, MetricParams())
    assert again.companies == 1


def test_metrics_version_changes_with_the_metrics_params_only():
    base = metrics_version(MetricParams())
    assert metrics_version(MetricParams()) == base
    assert metrics_version(MetricParams(cut_tolerance=0.02)) != base
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_metrics_current.py`
Expected: FAIL (`ImportError: cannot import name 'build_metrics' from 'dgi.metrics'`).

- [ ] **Step 3: Write the SQL files**

`src/dgi/metrics/sql/company_dim.sql`:
```sql
INSERT INTO company_dim BY NAME
SELECT cik, name, ticker, sic, sic_description
FROM stg_company
WHERE ticker IS NOT NULL AND ticker <> ''
QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY cik) = 1;
```

`src/dgi/metrics/sql/price_yearend.sql`:
```sql
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
```

`price_dividend.sql` builds four temp tables in one file: `tmp_price` (latest bar per ticker), `tmp_dividend_ttm` (non-special payments in the last 365 days, specials in the last five years), `tmp_yield_avg` (average of dividend per share over year-end close for the last five complete years, at least three required), `tmp_suspect` (payments at or above the previous year-end close).

`src/dgi/metrics/sql/price_dividend.sql`:
```sql
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
WHERE p.amount_adj >= y.close_adj
GROUP BY p.ticker;
```

`src/dgi/metrics/sql/fund_metrics.sql`:
```sql
CREATE OR REPLACE TEMP TABLE tmp_fund_metrics AS
WITH latest AS (
    SELECT ticker, fiscal_year AS fy FROM fundamentals_annual
    WHERE net_income IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY ticker ORDER BY fiscal_year DESC) = 1
),
cur AS (
    SELECT f.*, l.fy FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND l.fy = f.fiscal_year
),
ago AS (
    SELECT f.ticker, f.revenue, f.eps_diluted, f.fcf_per_share, f.shares_diluted
    FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND l.fy - 5 = f.fiscal_year
),
win5 AS (
    SELECT f.ticker,
           capped_ratio(sum(f.dividends_paid), sum(f.net_income), (SELECT payout_cap FROM run_params)) AS payout_earnings_5y,
           capped_ratio(sum(f.dividends_paid), sum(f.fcf), (SELECT payout_cap FROM run_params)) AS payout_fcf_5y,
           CASE WHEN count(f.op_margin) >= 3 THEN stddev_pop(f.op_margin) END AS op_margin_std
    FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND f.fiscal_year BETWEEN l.fy - 4 AND l.fy
    GROUP BY f.ticker
),
win10 AS (
    SELECT f.ticker, count(*) FILTER (WHERE f.net_income > 0)::INTEGER AS positive_earnings_years
    FROM fundamentals_annual f JOIN latest l ON l.ticker = f.ticker AND f.fiscal_year BETWEEN l.fy - 9 AND l.fy
    GROUP BY f.ticker
)
SELECT c.ticker, c.fy AS latest_fy, c.period_end AS fy_period_end,
       c.payout_earnings, c.payout_fcf, w5.payout_earnings_5y, w5.payout_fcf_5y, w5.op_margin_std,
       CASE WHEN c.operating_income IS NULL THEN NULL
            WHEN COALESCE(c.interest_expense, 0) <= 0 THEN (SELECT ratio_cap FROM run_params)
            ELSE least(c.operating_income / c.interest_expense, (SELECT ratio_cap FROM run_params)) END AS interest_coverage,
       CASE WHEN c.net_debt IS NULL OR c.ebitda IS NULL THEN NULL
            WHEN c.net_debt <= 0 THEN 0.0
            WHEN c.ebitda <= 0 THEN (SELECT ratio_cap FROM run_params)
            ELSE least(c.net_debt / c.ebitda, (SELECT ratio_cap FROM run_params)) END AS net_debt_ebitda,
       c.current_assets / NULLIF(c.current_liabilities, 0) AS current_ratio,
       w10.positive_earnings_years,
       cagr(a.revenue, c.revenue, 5) AS rev_cagr_5,
       cagr(a.eps_diluted, c.eps_diluted, 5) AS eps_cagr_5,
       cagr(a.fcf_per_share, c.fcf_per_share, 5) AS fcf_ps_cagr_5,
       CASE WHEN c.equity > 0 THEN c.net_income / c.equity END AS roe,
       cagr(a.shares_diluted, c.shares_diluted, 5) AS share_trend_5,
       c.net_income / NULLIF(c.shares_diluted, 0) AS eps_fy,
       c.fcf_per_share AS fcf_ps_fy,
       c.fcf AS fcf_latest, c.net_income AS net_income_latest
FROM cur c
LEFT JOIN ago a ON a.ticker = c.ticker
LEFT JOIN win5 w5 ON w5.ticker = c.ticker
LEFT JOIN win10 w10 ON w10.ticker = c.ticker;
```

`src/dgi/metrics/sql/ttm.sql`:
```sql
CREATE OR REPLACE TEMP TABLE tmp_ttm AS
WITH quarters AS (
    SELECT ticker, concept, period_end, value, filed FROM stg_income_quarter WHERE data_flag IS NULL
    UNION ALL
    SELECT ticker, concept, period_end, value, filed FROM stg_cash_quarter WHERE data_flag IS NULL
),
ranked AS (
    SELECT ticker, concept, period_end, value, filed,
           row_number() OVER (PARTITION BY ticker, concept ORDER BY period_end DESC) AS rn
    FROM quarters
    WHERE ticker IS NOT NULL AND value IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY ticker, concept, period_end ORDER BY filed DESC) = 1
),
sums AS (
    SELECT ticker, concept, sum(value) AS total, max(period_end) AS last_end,
           date_diff('day', min(period_end), max(period_end)) AS span
    FROM ranked WHERE rn <= 4 AND concept IN ('net_income', 'cash_from_operations', 'capex')
    GROUP BY ticker, concept HAVING count(*) = 4
),
wide AS (
    SELECT ticker, max(last_end) AS ttm_end,
           count(DISTINCT last_end) AS distinct_ends,
           max(total) FILTER (WHERE concept = 'net_income') AS ni_ttm,
           max(total) FILTER (WHERE concept = 'cash_from_operations') AS cfo_ttm,
           abs(max(total) FILTER (WHERE concept = 'capex')) AS capex_ttm,
           count(*) AS n_concepts,
           min(span) AS min_span, max(span) AS max_span
    FROM sums GROUP BY ticker
),
shares AS (
    SELECT r.ticker, r.period_end, r.value * COALESCE(f.factor, 1.0) AS shares_ttm
    FROM ranked r
    LEFT JOIN LATERAL (
        SELECT exp(sum(ln(s.ratio))) AS factor
        FROM stg_splits s WHERE s.ticker = r.ticker AND s.ex_date > r.filed AND s.ratio > 0
    ) f ON TRUE
    WHERE r.concept = 'shares_diluted_weighted' AND r.rn = 1
)
SELECT w.ticker, w.ttm_end, w.ni_ttm, w.cfo_ttm, w.capex_ttm, s.shares_ttm
FROM wide w
JOIN shares s ON s.ticker = w.ticker AND s.period_end = w.ttm_end
WHERE w.n_concepts = 3 AND w.distinct_ends = 1
  AND w.min_span >= (SELECT ttm_min_span_days FROM run_params)
  AND w.max_span <= (SELECT ttm_max_span_days FROM run_params);
```

`src/dgi/metrics/sql/shares_out.sql`:
```sql
CREATE OR REPLACE TEMP TABLE tmp_shares_out AS
SELECT r.ticker, r.value * COALESCE(f.factor, 1.0) AS shares_out
FROM (
    SELECT ticker, concept, period_end, value, filed FROM stg_shares_cover
    WHERE ticker IS NOT NULL AND value > 0 AND data_flag IS NULL
    QUALIFY row_number() OVER (
        PARTITION BY ticker
        ORDER BY period_end DESC, CASE concept WHEN 'shares_outstanding_cover' THEN 0 ELSE 1 END, filed DESC) = 1
) r
LEFT JOIN LATERAL (
    SELECT exp(sum(ln(s.ratio))) AS factor
    FROM stg_splits s WHERE s.ticker = r.ticker AND s.ex_date > r.filed AND s.ratio > 0
) f ON TRUE;
```

`src/dgi/metrics/sql/metrics_current.sql`:
```sql
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
```

- [ ] **Step 4: Replace `src/dgi/metrics/__init__.py`**

`src/dgi/metrics/__init__.py`:
```python
"""Derived metrics: SQL over the staged API data plus the streak code that does not belong in SQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from dgi.metrics.dividends import build_dividend_metrics
from dgi.metrics.params import MetricParams, params_hash
from dgi.metrics.sqlrun import create_run_params, run_sql_file
from dgi.schema import recreate_tables

METRICS_VERSION = "1"
METRIC_TABLES = ("company_dim", "dividend_payment", "dividend_annual", "price_yearend", "fundamentals_annual", "metrics_current")


@dataclass(frozen=True)
class MetricsResult:
    companies: int
    dividend_payments: int
    with_streak: int


def metrics_version(params: MetricParams) -> str:
    """Changes when the metrics code or the `metrics:` section of scoring.yaml changes."""
    return f"{METRICS_VERSION}:{params_hash(params)[:12]}"


def build_metrics(con: duckdb.DuckDBPyConnection, today: date, params: MetricParams) -> MetricsResult:
    """Build every metrics table from the stg_* tables already staged on `con`."""
    recreate_tables(con, METRIC_TABLES)
    create_run_params(con, today, params)
    for name in ("company_dim", "dividend_payment", "dividend_annual", "price_yearend", "fundamentals_annual"):
        run_sql_file(con, name)
    build_dividend_metrics(con, today, params.raise_tolerance, params.cut_tolerance)
    for name in ("price_dividend", "fund_metrics", "ttm", "shares_out", "metrics_current"):
        run_sql_file(con, name)
    companies = con.execute("SELECT count(*) FROM metrics_current").fetchone()[0]
    payments = con.execute("SELECT count(*) FROM dividend_payment").fetchone()[0]
    with_streak = con.execute("SELECT count(*) FROM metrics_current WHERE streak IS NOT NULL").fetchone()[0]
    return MetricsResult(companies, payments, with_streak)
```

- [ ] **Step 5: Run to verify it passes, then the whole suite**

Run: `uv run pytest tests/test_metrics_current.py`
Expected: PASS (14 passed).

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/dgi/metrics tests/test_metrics_current.py
git commit -m "feat: current metrics (yield history, fundamentals windows, TTM basis) and build_metrics"
```
