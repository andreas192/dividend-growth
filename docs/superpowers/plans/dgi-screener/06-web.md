# Phase 6: Web UI (Tasks 18-22)

Part of `../2026-10-05-dgi-screener.md` (read its Global Constraints, Review Focus and "Spec clarifications" first). Spec: `../../specs/2026-10-05-dgi-screener-design.md`, section "Views".

Rules for this phase:

- `web` imports **only** `dgi.cache` (plus `dgi.settings` and `dgi.errors`). `dgi/cache/__init__.py` re-exports what the web layer needs from `scoring` (`HardFilters`, `PILLARS`, `ScoringConfig`, fair value helpers). `tests/test_architecture.py` enforces this.
- The web app never reaches the API directly. The company page's daily charts use a `PriceSource` injected by `pipeline` (Task 22); with no source, or with the API down, the page keeps working on annual figures and says so.
- Every query value is a bound parameter. Sort columns are a fixed whitelist. User text (company names) is escaped by Jinja or written with `textContent`/an escape function in JS.
- Scores shown for re-weighted pillars are a weighted sum of the stored pillar scores over the pillars a company has; bands are never recomputed in the web layer.
- DuckDB caches database instances per path inside a process: `CacheHandle` closes its connection before reopening a replaced file.

Chart design follows the `dataviz` skill (loaded while writing this plan). Choices and checks that carry into Task 21:

- Forms: bars for a yearly magnitude (dividend per share); lines for change over time; a scatter (size = score) for the screener. Two measures with different scales are two charts, never a dual axis (net debt / EBITDA and interest coverage are separate charts; dividend growth is its own line chart, not a second axis on the bars).
- Colour: categorical slots 1-3 only (blue `#2a78d6`, orange `#eb6834`, aqua `#1baf7a`; dark `#3987e5`, `#d95926`, `#199e70`), assigned by series order in `ChartSeries.slot`, never by rank. Checked with the skill's validator in both modes, adjacent pairs and all pairs: every hard gate passes (worst CVD distance 9.2 light / 9.4 dark, normal-vision 24.0 / 20.9); the aqua slot is under 3:1 on the light surface, which obliges visible legends and a table view, and every chart ships both. Never use more than three series in one chart; a fourth would be folded or faceted.
- Marks: 2px lines, no point markers, 4px rounded bar tops anchored to the baseline, hairline grid, dashed reference lines labelled in words ("5-year average", "100%"), no dual axis, a legend whenever there are two or more series, crosshair tooltip listing every series with the value first.
- Every chart has a "Table" disclosure built from the same data, so no value depends on hover. Text uses ink tokens, never series colours. Status/warning text pairs the colour with a symbol and a word ("▲ Warning:").
- Light and dark are separate token sets (`app.css`), switched by the OS setting or the Theme button; charts re-read the tokens when the theme changes.

---

### Task 18: Cache handle and screener queries

**Files:**
- Create: `src/dgi/cache/handle.py`, `src/dgi/cache/screener.py`; replace `src/dgi/cache/__init__.py` (re-exports; the company names arrive in Task 19)
- Test: `tests/web_fixtures.py`, `tests/test_cache_handle.py`, `tests/test_cache_screener.py`

**Interfaces:**
- Consumes: tables `scores`, `company_dim`, `metrics_current`, `flags` (Tasks 10, 14); `HardFilters`, `PILLARS` (Task 11).
- Produces:
  - `CacheHandle(path)` with `connection()` (context manager yielding a read-only DuckDB connection, or `None` when there is no usable cache; it reopens when the file's inode or modification time changes, closing the old connection first) and `close()`.
  - `parse_query(params: Mapping[str, str], defaults: HardFilters, pillar_weights: Mapping[str, float]) -> ScreenerQuery`, raising `BadQuery(param, reason)`. URL units: yields and payouts in percent (`5` = 5%), market cap in billions (`min_cap_bn`), pillar weights `w_dividend|w_safety|w_growth|w_valuation` as 0-100, `sort` in `score|ticker|name|sector|price|yield|streak|dgr5|payout_fcf|dividend|safety|growth|valuation|cap|mos`, `dir` `asc|desc`, `page`, `size` (10-200), `sector`, `unscored=1`. A blank limit field means no limit. `ScreenerQuery.params` holds the normalized URL parameters so links keep the state.
  - `query_screener(con, q) -> ScreenerPage(rows, total, page, pages)` (page clamped to the last page; each `ScreenerRow` carries `years_history` for the streak label), `export_rows(con, q)`, `universe_stats(con, q) -> UniverseStats(universe, scored, passing, median_yield)`, `scatter_points(con, q)` (up to 3000 points), `query_unscored(con, q) -> (rows, total)`, `sector_groups(con)`. A company with an unknown value fails a limit that is set; ties sort by ticker and NULLs sort last.
  - Test helpers: `tests/web_fixtures.py` with `COMPANIES` (AAA, BBB pass the default filters; CCC pays out 110% of FCF; DDD has a 4-year streak; EEE is under $1B; BANK and NEWC are not scored), `add_history(con, ticker)`, `sample_cache(path) -> Path`.

- [ ] **Step 1: Write the failing tests and fixtures**

`tests/web_fixtures.py` (a small but complete cache file for the cache-read and web tests):
```python
"""A small but complete cache file for the cache-read and web tests: scored, filtered and not-scored companies."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb

from dgi import schema
from tests.cache_fixtures import make_meta, persist, scored_cache

D = dt.date

# ticker -> (sic, metric overrides). With the default filters only AAA and BBB pass:
# CCC pays out more than its free cash flow, DDD has a short streak, EEE is below $1B.
COMPANIES: dict[str, tuple[str, dict[str, Any]]] = {
    "AAA": ("2080", {"streak": 30, "years_history": 45, "dgr_5": 0.07, "div_yield": 0.025, "market_cap": 8e9}),
    "BBB": ("2834", {"streak": 12, "dgr_5": 0.04, "div_yield": 0.045, "market_cap": 3e9, "payout_fcf": 0.7}),
    "CCC": ("1311", {"streak": 8, "dgr_5": 0.02, "div_yield": 0.08, "market_cap": 2e9, "payout_fcf": 1.1}),
    "DDD": ("4911", {"streak": 4, "dgr_5": 0.03, "div_yield": 0.035, "market_cap": 6e9}),
    "EEE": ("2080", {"streak": 15, "years_history": 20, "dgr_5": 0.05, "div_yield": 0.03, "market_cap": 5e8}),
    "BANK": ("6021", {}),
    "NEWC": ("3000", {"years_history": 3}),
}


def add_history(con: duckdb.DuckDBPyConnection, ticker: str) -> None:
    """Annual dividends 2014-2025 (+5% a year), fundamentals 2015-2025, year-end prices, and quarterly payments."""
    schema.insert_rows(con, "dividend_annual", [
        {"ticker": ticker, "year": y, "dps": round(1.0 * 1.05 ** (y - 2014), 4), "n_payments": 4, "special_total": 0.0, "complete": True}
        for y in range(2014, 2026)
    ])
    schema.insert_rows(con, "fundamentals_annual", [
        {"ticker": ticker, "fiscal_year": y, "period_end": D(y, 12, 31), "net_income": 100.0 * 1.05 ** (y - 2015), "eps_diluted": 2.0 * 1.05 ** (y - 2015),
         "fcf_per_share": 2.2 * 1.05 ** (y - 2015), "shares_diluted": 100e6 * 0.99 ** (y - 2015), "operating_income": 150.0, "interest_expense": 10.0,
         "net_debt": 150.0, "ebitda": 180.0, "payout_earnings": 0.5, "payout_fcf": 0.45 if y < 2025 else 9.99}
        for y in range(2015, 2026)
    ])
    schema.insert_rows(con, "price_yearend", [{"ticker": ticker, "year": y, "close_adj": 20.0 * 1.1 ** (y - 2014)} for y in range(2014, 2026)])
    schema.insert_rows(con, "dividend_payment", [
        {"ticker": ticker, "ex_date": D(y, m, 10), "year": y, "amount_adj": round(0.25 * 1.05 ** (y - 2014), 4), "is_special": False}
        for y in range(2014, 2026) for m in (2, 5, 8, 11)
    ] + [{"ticker": ticker, "ex_date": D(2026, 2, 10), "year": 2026, "amount_adj": 0.5, "is_special": True}])


def sample_cache(path: Path) -> Path:
    con = scored_cache(COMPANIES)
    for ticker in ("AAA", "BBB", "CCC"):
        add_history(con, ticker)
    persist(con, path, make_meta())
    con.close()
    return path
```

`tests/test_cache_handle.py` (UNIT: CacheHandle):
```python
import duckdb

from dgi.cache.handle import CacheHandle
from dgi.cache.build import new_path, swap_in
from tests.cache_fixtures import cache_with, make_meta, persist


def tickers(handle):
    with handle.connection() as con:
        return None if con is None else [r[0] for r in con.execute("SELECT ticker FROM company_dim ORDER BY 1").fetchall()]


def test_no_file_means_no_connection(tmp_path):
    assert tickers(CacheHandle(tmp_path / "dgi.duckdb")) is None


def test_an_existing_cache_is_read(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    handle = CacheHandle(path)
    assert tickers(handle) == ["AAA"]
    assert tickers(handle) == ["AAA"]  # reuses the connection
    handle.close()


def test_a_swapped_cache_is_picked_up_without_a_restart(tmp_path):
    live = persist(cache_with({"OLD": ("2080", {})}), tmp_path / "dgi.duckdb")
    handle = CacheHandle(live)
    assert tickers(handle) == ["OLD"]
    swap_in(persist(cache_with({"NEW": ("2080", {})}), new_path(live)), live)
    assert tickers(handle) == ["NEW"]   # the old connection was closed first; DuckDB would otherwise keep serving the old file
    handle.close()


def test_a_cache_that_appears_later_is_picked_up_and_one_that_disappears_is_dropped(tmp_path):
    path = tmp_path / "dgi.duckdb"
    handle = CacheHandle(path)
    assert tickers(handle) is None
    persist(cache_with({"AAA": ("2080", {})}), path)
    assert tickers(handle) == ["AAA"]
    path.unlink()
    assert tickers(handle) is None
    handle.close()


def test_a_corrupt_file_reads_as_no_cache(tmp_path):
    path = tmp_path / "dgi.duckdb"
    path.write_bytes(b"garbage" * 200)
    handle = CacheHandle(path)
    assert tickers(handle) is None
    handle.close()
```

`tests/test_cache_screener.py` (UNIT: parse_query, query_screener, stats, export, scatter, unscored):
```python
import pytest

from dgi.cache import (
    BadQuery, HardFilters, PILLARS, export_rows, parse_query, query_screener, query_unscored, scatter_points, sector_groups, universe_stats,
)
from tests.cache_fixtures import CFG, scored_cache
from tests.web_fixtures import COMPANIES

WEIGHTS = CFG.pillars


def query(**params):
    return parse_query({k: str(v) for k, v in params.items()}, CFG.hard_filters, WEIGHTS)


@pytest.fixture(scope="module")
def con():
    return scored_cache(COMPANIES)


def tickers(page):
    return [r.ticker for r in page.rows]


# parse_query ---------------------------------------------------------------------------------------------------------

def test_defaults_come_from_the_config():
    q = query()
    assert (q.min_streak, q.max_payout_fcf, q.max_payout_eps, q.min_market_cap, q.min_yield, q.max_yield) == (5, 1.0, None, 1e9, 0.0, None)
    assert q.weights == {"dividend": 30.0, "safety": 30.0, "growth": 20.0, "valuation": 20.0}
    assert (q.sort, q.descending, q.page, q.size, q.show_unscored, q.sector) == ("score", True, 1, 50, False, None)
    assert q.params["max_payout_fcf"] == "100" and q.params["min_cap_bn"] == "1" and q.params["max_payout_eps"] == ""


def test_percent_and_billions_are_converted():
    q = query(max_payout_fcf=80, min_yield=2, max_yield=6.5, min_cap_bn=2.5, max_payout_eps=60)
    assert (q.max_payout_fcf, q.min_yield, q.max_yield, q.min_market_cap, q.max_payout_eps) == (0.8, 0.02, 0.065, 2.5e9, 0.6)


def test_a_blank_nullable_field_means_no_limit_and_a_blank_plain_field_means_default():
    q = query(max_payout_fcf="", max_yield="", min_streak="")
    assert q.max_payout_fcf is None and q.max_yield is None and q.min_streak == 5


def test_the_normalized_params_round_trip():
    q = query(min_streak=9, max_payout_fcf=75, sort="yield", dir="asc", sector="Energy", unscored=1, w_growth=0)
    again = parse_query(q.params, CFG.hard_filters, WEIGHTS)
    assert again == q


def test_sort_defaults_ascending_for_text_columns():
    assert query(sort="ticker").descending is False and query(sort="yield").descending is True


@pytest.mark.parametrize(
    "params, param",
    [
        ({"min_streak": "abc"}, "min_streak"), ({"min_streak": "-1"}, "min_streak"), ({"min_streak": "1.5"}, "min_streak"),
        ({"max_payout_fcf": "x"}, "max_payout_fcf"), ({"min_yield": "101"}, "min_yield"), ({"min_cap_bn": "nan"}, "min_cap_bn"),
        ({"page": "0"}, "page"), ({"page": "x"}, "page"), ({"size": "5"}, "size"), ({"size": "5000"}, "size"),
        ({"sort": "ticker;drop table scores"}, "sort"), ({"sort": "nope"}, "sort"), ({"dir": "sideways"}, "dir"),
        ({"w_safety": "-3"}, "w_safety"), ({"w_dividend": "0", "w_safety": "0", "w_growth": "0", "w_valuation": "0"}, "w_dividend"),
        ({"min_score": "101"}, "min_score"),
    ],
)
def test_bad_input_raises_bad_query_naming_the_parameter(params, param):
    with pytest.raises(BadQuery) as exc:
        query(**params)
    assert exc.value.param == param


# screening -----------------------------------------------------------------------------------------------------------

def test_default_filters_keep_only_the_companies_that_pass_every_limit(con):
    page = query_screener(con, query())
    assert tickers(page) == ["AAA", "BBB"]  # AAA has the longer streak, so it ranks first
    assert page.total == 2 and page.pages == 1 and page.rows[0].position == 1 and page.rows[1].position == 2


def test_each_hard_filter_removes_its_company_and_loosening_it_brings_it_back(con):
    assert "CCC" not in tickers(query_screener(con, query()))
    assert "CCC" in tickers(query_screener(con, query(max_payout_fcf=120)))      # payout 110% allowed
    assert "CCC" in tickers(query_screener(con, query(max_payout_fcf="")))       # no payout limit
    assert "DDD" in tickers(query_screener(con, query(min_streak=0)))
    assert "EEE" in tickers(query_screener(con, query(min_cap_bn=0)))
    assert tickers(query_screener(con, query(min_streak=20))) == ["AAA"]
    assert tickers(query_screener(con, query(min_yield=4))) == ["BBB"]
    assert tickers(query_screener(con, query(max_yield=3))) == ["AAA"]


def test_unknown_values_fail_a_limit_that_is_set():
    con = scored_cache({"AAA": ("2080", {"payout_fcf": None})})
    assert query_screener(con, query(max_payout_fcf=100)).total == 0
    assert query_screener(con, query(max_payout_fcf="")).total == 1


def test_sector_filter_and_list(con):
    assert tickers(query_screener(con, query(sector="Health Care"))) == ["BBB"]
    assert query_screener(con, query(sector="Nowhere")).total == 0
    assert "Consumer Staples" in sector_groups(con) and "Energy" in sector_groups(con)


def test_sorting_ties_break_by_ticker_and_nulls_go_last(con):
    everything = dict(min_streak=0, max_payout_fcf="", min_cap_bn=0)
    assert tickers(query_screener(con, query(sort="ticker", **everything))) == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert tickers(query_screener(con, query(sort="yield", **everything)))[0] == "CCC"
    assert tickers(query_screener(con, query(sort="streak", dir="asc", **everything)))[0] == "DDD"


def test_paging_reports_total_pages_and_clamps_a_page_past_the_end(con):
    everything = dict(min_streak=0, max_payout_fcf="", min_cap_bn=0, size=10)
    first = query_screener(con, query(**everything))
    assert (first.total, first.pages, len(first.rows)) == (5, 1, 5)
    assert query_screener(con, query(page=9, **everything)).page == 1


def test_reweighting_changes_the_score_without_recomputing_bands():
    con = scored_cache({"AAA": ("2080", {}), "BBB": ("2834", {"streak": 6, "payout_fcf": 0.95, "pe": 38.0})})
    all_pillars = {r.ticker: r.score for r in query_screener(con, query()).rows}
    only_dividend = {r.ticker: r.score for r in query_screener(con, query(w_safety=0, w_growth=0, w_valuation=0)).rows}
    stored = dict(con.execute("SELECT ticker, dividend FROM scores").fetchall())
    assert only_dividend == pytest.approx(stored)          # a weighted sum of the stored pillar score
    assert all_pillars != pytest.approx(only_dividend)


def test_a_pillar_a_company_lacks_is_left_out_of_its_weighted_mean():
    con = scored_cache({"AAA": ("2080", {})})
    con.execute("UPDATE scores SET valuation = NULL")
    score = query_screener(con, query()).rows[0].score
    row = con.execute("SELECT dividend, safety, growth FROM scores").fetchone()
    assert score == pytest.approx((row[0] * 30 + row[1] * 30 + row[2] * 20) / 80)


def test_rows_carry_the_history_length_that_the_streak_label_needs(con):
    rows = {r.ticker: r for r in query_screener(con, query()).rows}
    assert (rows["AAA"].streak, rows["AAA"].years_history, rows["BBB"].years_history) == (30, 45, 12)


def test_rows_carry_red_flags(con):
    rows = {r.ticker: r for r in query_screener(con, query(max_payout_fcf="")).rows}
    assert rows["CCC"].red_flags == ["payout_fcf_over_100"] and rows["AAA"].red_flags == []


def test_the_min_score_filter(con):
    best = query_screener(con, query()).rows[0].score
    assert query_screener(con, query(min_score=best + 0.01)).total == 0


def test_universe_stats_and_export_and_scatter(con):
    stats = universe_stats(con, query())
    assert (stats.universe, stats.scored, stats.passing) == (7, 5, 2)
    assert stats.median_yield == pytest.approx((0.025 + 0.045) / 2)
    exported = export_rows(con, query())
    assert [r.ticker for r in exported] == tickers(query_screener(con, query()))
    points = scatter_points(con, query())
    assert {p.ticker for p in points} == {"AAA", "BBB"} and all(p.div_yield and p.dgr_5 for p in points)


def test_unscored_companies_are_listed_with_their_reason(con):
    rows, total = query_unscored(con, query())
    assert total == 2 and {(r.ticker, r.reason) for r in rows} == {("BANK", "financial_or_reit"), ("NEWC", "short_dividend_history")}
    assert query_unscored(con, query(sector="Energy"))[1] == 0


def test_every_pillar_name_is_known_to_the_query_layer():
    assert PILLARS == ("dividend", "safety", "growth", "valuation")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cache_handle.py tests/test_cache_screener.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.cache.handle'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/cache/handle.py`:
```python
"""A read-only connection to the live cache that follows atomic swaps."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


class CacheHandle:
    """Opens the cache read-only and reopens it when the file is replaced.

    DuckDB keeps one instance per path inside a process, so after a swap a plain `connect()` would keep
    serving the old file: the old connection is closed first. One lock serializes readers; the app has one user.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._stamp: tuple[int, int] | None = None

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_mtime_ns

    def _refresh(self) -> None:
        stamp = self._stat()
        if stamp == self._stamp and (self._con is not None or stamp is None):
            return
        if self._con is not None:
            self._con.close()
            self._con = None
        self._stamp = stamp
        if stamp is not None:
            try:
                self._con = duckdb.connect(str(self._path), read_only=True)
            except duckdb.Error:
                self._con = None  # unreadable: served as "no cache" until the next swap

    @contextmanager
    def connection(self) -> Iterator[duckdb.DuckDBPyConnection | None]:
        """The current connection, or None when there is no usable cache."""
        with self._lock:
            self._refresh()
            yield self._con

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
            self._stamp = None
```

`src/dgi/cache/screener.py`:
```python
"""The screener's read-only queries: URL parameters in, ranked rows out. Every value is a bound parameter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from dgi.scoring.config import PILLARS, HardFilters

SORTS = {
    "score": "score", "ticker": "ticker", "name": "name", "sector": "sector_group", "price": "price", "yield": "div_yield",
    "streak": "streak", "dgr5": "dgr_5", "payout_fcf": "payout_fcf", "dividend": "dividend", "safety": "safety",
    "growth": "growth", "valuation": "valuation", "cap": "market_cap", "mos": "margin_of_safety",
}
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
SCATTER_LIMIT = 3000


class BadQuery(ValueError):
    def __init__(self, param: str, reason: str) -> None:
        super().__init__(f"{param}: {reason}")
        self.param = param
        self.reason = reason


@dataclass(frozen=True)
class ScreenerQuery:
    min_streak: int
    max_payout_fcf: float | None   # fractions here; the URL uses percent
    max_payout_eps: float | None
    min_market_cap: float
    min_yield: float
    max_yield: float | None
    min_score: float
    sector: str | None
    show_unscored: bool
    weights: dict[str, float]
    sort: str
    descending: bool
    page: int
    size: int
    params: dict[str, str]         # the normalized URL parameters, for links that keep the state


@dataclass(frozen=True)
class ScreenerRow:
    ticker: str
    name: str
    sector: str | None
    price: float | None
    div_yield: float | None
    streak: int | None
    years_history: int | None
    dgr_5: float | None
    payout_fcf: float | None
    payout_earnings: float | None
    market_cap: float | None
    dividend: float | None
    safety: float | None
    growth: float | None
    valuation: float | None
    margin_of_safety: float | None
    score: float | None
    position: int
    red_flags: list[str]


@dataclass(frozen=True)
class ScreenerPage:
    rows: list[ScreenerRow]
    total: int
    page: int
    pages: int


@dataclass(frozen=True)
class UniverseStats:
    universe: int
    scored: int
    passing: int
    median_yield: float | None


@dataclass(frozen=True)
class UnscoredRow:
    ticker: str
    name: str
    sector: str | None
    reason: str


@dataclass(frozen=True)
class ScatterPoint:
    ticker: str
    name: str
    div_yield: float
    dgr_5: float
    score: float | None


def _number(params: Mapping[str, str], name: str, default: float | None, low: float, high: float, nullable: bool = False) -> float | None:
    raw = params.get(name)
    if raw is None:
        return default
    if raw.strip() == "":
        return None if nullable else default
    try:
        value = float(raw)
    except ValueError:
        raise BadQuery(name, "must be a number") from None
    if not low <= value <= high:
        raise BadQuery(name, f"must be between {low:g} and {high:g}")
    return value


def _int(params: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = params.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadQuery(name, "must be a whole number") from None
    if not low <= value <= high:
        raise BadQuery(name, f"must be between {low} and {high}")
    return value


def _show(value: float | None, scale: float = 1.0) -> str:
    return "" if value is None else f"{value * scale:g}"


def parse_query(params: Mapping[str, str], defaults: HardFilters, pillar_weights: Mapping[str, float]) -> ScreenerQuery:
    """Read the screener's URL parameters. Percent values (yield, payout) are written as 5 for 5%."""
    min_streak = _int(params, "min_streak", defaults.min_streak, 0, 100)
    payout_fcf = _number(params, "max_payout_fcf", None if defaults.max_payout_fcf is None else defaults.max_payout_fcf * 100, 0, 10000, True)
    payout_eps = _number(params, "max_payout_eps", None if defaults.max_payout_eps is None else defaults.max_payout_eps * 100, 0, 10000, True)
    cap_bn = _number(params, "min_cap_bn", defaults.min_market_cap / 1e9, 0, 10000)
    min_yield = _number(params, "min_yield", defaults.min_yield * 100, 0, 100)
    max_yield = _number(params, "max_yield", None if defaults.max_yield is None else defaults.max_yield * 100, 0, 100, True)
    min_score = _number(params, "min_score", defaults.min_score, 0, 100)
    weights = {p: _number(params, f"w_{p}", pillar_weights[p] * 100, 0, 100) for p in PILLARS}
    if sum(weights.values()) <= 0:
        raise BadQuery("w_dividend", "at least one pillar weight must be above zero")
    sort = params.get("sort", "score")
    if sort not in SORTS:
        raise BadQuery("sort", f"must be one of {', '.join(SORTS)}")
    direction = params.get("dir", "asc" if sort in ("ticker", "name", "sector") else "desc")
    if direction not in ("asc", "desc"):
        raise BadQuery("dir", "must be asc or desc")
    page = _int(params, "page", 1, 1, 100000)
    size = _int(params, "size", PAGE_SIZE, 10, MAX_PAGE_SIZE)
    sector = params.get("sector") or None
    unscored = params.get("unscored") == "1"
    normalized = {
        "min_streak": str(min_streak), "max_payout_fcf": _show(payout_fcf), "max_payout_eps": _show(payout_eps),
        "min_cap_bn": _show(cap_bn), "min_yield": _show(min_yield), "max_yield": _show(max_yield), "min_score": _show(min_score),
        "sort": sort, "dir": direction, **{f"w_{p}": _show(w) for p, w in weights.items()},
    }
    if sector:
        normalized["sector"] = sector
    if unscored:
        normalized["unscored"] = "1"
    return ScreenerQuery(
        min_streak=min_streak,
        max_payout_fcf=None if payout_fcf is None else payout_fcf / 100,
        max_payout_eps=None if payout_eps is None else payout_eps / 100,
        min_market_cap=cap_bn * 1e9,
        min_yield=min_yield / 100,
        max_yield=None if max_yield is None else max_yield / 100,
        min_score=min_score,
        sector=sector,
        show_unscored=unscored,
        weights=weights,
        sort=sort,
        descending=direction == "desc",
        page=page,
        size=size,
        params=normalized,
    )


def _score_expression(weights: Mapping[str, float]) -> tuple[str, list[float]]:
    """Pillar scores re-weighted over the pillars a company has: a weighted sum, no recomputation of bands."""
    numerator = " + ".join(f"COALESCE(s.{p}, 0) * ?" for p in PILLARS)
    denominator = " + ".join(f"CASE WHEN s.{p} IS NULL THEN 0 ELSE ? END" for p in PILLARS)
    values = [weights[p] for p in PILLARS]
    return f"({numerator}) / NULLIF({denominator}, 0)", values + values


def _filters(q: ScreenerQuery) -> tuple[str, list[Any]]:
    clauses = ["COALESCE(streak, 0) >= ?", "COALESCE(market_cap, 0) >= ?", "COALESCE(score, 0) >= ?", "COALESCE(div_yield, 0) >= ?"]
    values: list[Any] = [q.min_streak, q.min_market_cap, q.min_score, q.min_yield]
    for column, limit in (("payout_fcf", q.max_payout_fcf), ("payout_earnings", q.max_payout_eps), ("div_yield", q.max_yield)):
        if limit is not None:
            clauses.append(f"{column} <= ?")  # an unknown value fails a limit that is set
            values.append(limit)
    if q.sector:
        clauses.append("sector_group = ?")
        values.append(q.sector)
    return " AND ".join(clauses), values


def _ranked_cte(q: ScreenerQuery) -> tuple[str, list[Any]]:
    score_sql, score_values = _score_expression(q.weights)
    where, where_values = _filters(q)
    sql = f"""
WITH base AS (
    SELECT s.ticker, c.name, s.sector_group, m.price, m.div_yield, m.streak, m.years_history, m.dgr_5, m.payout_fcf, m.payout_earnings,
           m.market_cap, s.dividend, s.safety, s.growth, s.valuation, s.margin_of_safety, {score_sql} AS score
    FROM scores s
    JOIN company_dim c ON c.ticker = s.ticker
    JOIN metrics_current m ON m.ticker = s.ticker
    WHERE s.status = 'scored'
),
passing AS (SELECT * FROM base WHERE {where}),
ranked AS (SELECT *, rank() OVER (ORDER BY score DESC NULLS LAST)::INTEGER AS position FROM passing)"""
    return sql, score_values + where_values


def _order(q: ScreenerQuery) -> str:
    direction = "DESC" if q.descending else "ASC"
    return f"{SORTS[q.sort]} {direction} NULLS LAST, ticker ASC"


def _rows(cur: duckdb.DuckDBPyConnection) -> list[ScreenerRow]:
    names = [d[0] for d in cur.description]
    rows = []
    for values in cur.fetchall():
        r = dict(zip(names, values))
        rows.append(ScreenerRow(
            ticker=r["ticker"], name=r["name"], sector=r["sector_group"], price=r["price"], div_yield=r["div_yield"], streak=r["streak"],
            years_history=r["years_history"], dgr_5=r["dgr_5"], payout_fcf=r["payout_fcf"], payout_earnings=r["payout_earnings"],
            market_cap=r["market_cap"], dividend=r["dividend"], safety=r["safety"], growth=r["growth"], valuation=r["valuation"],
            margin_of_safety=r["margin_of_safety"], score=r["score"], position=r["position"], red_flags=list(r["red_flags"] or []),
        ))
    return rows


_SELECT_ROWS = """
SELECT ticker, name, sector_group, price, div_yield, streak, years_history, dgr_5, payout_fcf, payout_earnings, market_cap,
       dividend, safety, growth, valuation, margin_of_safety, score, position,
       (SELECT list(code ORDER BY code) FROM flags f WHERE f.ticker = ranked.ticker AND f.severity = 'red') AS red_flags
FROM ranked"""


def query_screener(con: duckdb.DuckDBPyConnection, q: ScreenerQuery) -> ScreenerPage:
    cte, values = _ranked_cte(q)
    total = con.execute(cte + " SELECT count(*) FROM passing", values).fetchone()[0]
    pages = max(1, -(-total // q.size))
    page = min(q.page, pages)
    cur = con.execute(f"{cte} {_SELECT_ROWS} ORDER BY {_order(q)} LIMIT ? OFFSET ?", values + [q.size, (page - 1) * q.size])
    return ScreenerPage(_rows(cur), total, page, pages)


def export_rows(con: duckdb.DuckDBPyConnection, q: ScreenerQuery) -> list[ScreenerRow]:
    cte, values = _ranked_cte(q)
    return _rows(con.execute(f"{cte} {_SELECT_ROWS} ORDER BY {_order(q)}", values))


def universe_stats(con: duckdb.DuckDBPyConnection, q: ScreenerQuery) -> UniverseStats:
    cte, values = _ranked_cte(q)
    universe = con.execute("SELECT count(*) FROM company_dim").fetchone()[0]
    scored = con.execute("SELECT count(*) FROM scores WHERE status = 'scored'").fetchone()[0]
    passing, median_yield = con.execute(cte + " SELECT count(*), median(div_yield) FROM passing", values).fetchone()
    return UniverseStats(universe, scored, passing, median_yield)


def scatter_points(con: duckdb.DuckDBPyConnection, q: ScreenerQuery) -> list[ScatterPoint]:
    cte, values = _ranked_cte(q)
    cur = con.execute(
        f"{cte} SELECT ticker, name, div_yield, dgr_5, score FROM ranked WHERE div_yield IS NOT NULL AND dgr_5 IS NOT NULL "
        "ORDER BY score DESC NULLS LAST, ticker LIMIT ?",
        values + [SCATTER_LIMIT],
    )
    return [ScatterPoint(*r) for r in cur.fetchall()]


def query_unscored(con: duckdb.DuckDBPyConnection, q: ScreenerQuery) -> tuple[list[UnscoredRow], int]:
    where, values = "s.status = 'not_scored'", []
    if q.sector:
        where += " AND s.sector_group = ?"
        values.append(q.sector)
    total = con.execute(f"SELECT count(*) FROM scores s WHERE {where}", values).fetchone()[0]
    pages = max(1, -(-total // q.size))
    page = min(q.page, pages)
    cur = con.execute(
        f"SELECT s.ticker, c.name, s.sector_group, s.reason FROM scores s JOIN company_dim c ON c.ticker = s.ticker "
        f"WHERE {where} ORDER BY s.reason, s.ticker LIMIT ? OFFSET ?",
        values + [q.size, (page - 1) * q.size],
    )
    return [UnscoredRow(*r) for r in cur.fetchall()], total


def sector_groups(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT sector_group FROM scores WHERE sector_group IS NOT NULL ORDER BY 1").fetchall()]
```

Replace `src/dgi/cache/__init__.py` (Task 19 adds the company names):

`src/dgi/cache/__init__.py`:
```python
"""The derived cache: meta and refresh planning, atomic build and swap, quality checks, read-only queries.

The web app imports only this package; the names it needs from `scoring` are re-exported here.
"""

from dgi.cache.handle import CacheHandle
from dgi.cache.meta import CacheMeta, read_meta_from
from dgi.cache.screener import (
    BadQuery, ScatterPoint, ScreenerPage, ScreenerQuery, ScreenerRow, UniverseStats, UnscoredRow, export_rows, parse_query,
    query_screener, query_unscored, scatter_points, sector_groups, universe_stats,
)
from dgi.cache.status import CacheStatus, read_status_from
from dgi.scoring.config import PILLARS, HardFilters, ScoringConfig, load_scoring_config
from dgi.scoring.valuation import FairValue, fair_value_range, grid_axes, sensitivity_grid

__all__ = [
    "BadQuery", "CacheHandle", "CacheMeta", "CacheStatus", "FairValue", "HardFilters", "PILLARS", "ScatterPoint",
    "ScoringConfig", "ScreenerPage", "ScreenerQuery", "ScreenerRow", "UniverseStats", "UnscoredRow", "export_rows",
    "fair_value_range", "grid_axes", "load_scoring_config", "parse_query",
    "query_screener", "query_unscored", "read_meta_from", "read_status_from", "scatter_points", "sector_groups", "sensitivity_grid", "universe_stats",
]
```

- [ ] **Step 4: Run to verify it passes, then the architecture test**

Run: `uv run pytest tests/test_cache_handle.py tests/test_cache_screener.py tests/test_architecture.py`
Expected: PASS (5 + 35 + 7 tests; the architecture test still skips the `web` package).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/cache tests/web_fixtures.py tests/test_cache_handle.py tests/test_cache_screener.py
git commit -m "feat: cache handle that follows swaps, and screener queries with weighted re-scoring"
```

---

### Task 19: Company queries

**Files:**
- Create: `src/dgi/cache/company.py`; replace `src/dgi/cache/__init__.py` with the final version
- Test: `tests/test_cache_company.py`

**Interfaces:**
- Consumes: tables `company_dim`, `scores`, `metrics_current`, `score_detail`, `flags`, `dividend_annual`, `fundamentals_annual`, `price_yearend`, `dividend_payment`.
- Produces:
  - `CompanyView(ticker, name, sic, sic_description, sector_group, status, reason, score: dict, metrics: dict, details: list[dict], flags: list[dict])`; `load_company(con, ticker) -> CompanyView | None` (case-insensitive; details ordered by pillar then contribution descending; flags red first).
  - `AnnualData(dividends, fundamentals, year_end_prices)`; `load_annual(con, ticker) -> AnnualData`; `load_payments(con, ticker) -> list[tuple[date, float]]` (regular payments only, oldest first).

- [ ] **Step 1: Write the failing tests**

`tests/test_cache_company.py` (UNIT: load_company, load_annual, load_payments):
```python
import datetime as dt

import pytest

from dgi.cache import load_annual, load_company, load_payments
from tests.cache_fixtures import scored_cache
from tests.web_fixtures import COMPANIES, add_history


@pytest.fixture(scope="module")
def con():
    c = scored_cache(COMPANIES)
    add_history(c, "AAA")
    return c


def test_a_scored_company_has_everything_the_page_needs(con):
    view = load_company(con, "AAA")
    assert (view.ticker, view.name, view.status, view.reason, view.sector_group) == ("AAA", "AAA Inc", "scored", None, "Consumer Staples")
    assert view.metrics["streak"] == 30 and view.score["total"] > 0
    assert [d["pillar"] for d in view.details] == sorted((d["pillar"] for d in view.details), key=("dividend", "safety", "growth", "valuation").index)
    contributions = [d["contribution"] for d in view.details if d["pillar"] == "dividend"]
    assert contributions == sorted(contributions, reverse=True)
    assert view.flags == []


def test_lookup_ignores_case_and_flags_come_red_first():
    con = scored_cache({"CCC": ("1311", {"payout_fcf": 1.1, "special_count_5y": 1})})
    view = load_company(con, "ccc")
    assert view.ticker == "CCC"
    assert [(f["code"], f["severity"]) for f in view.flags] == [("payout_fcf_over_100", "red"), ("special_dividend", "info")]


def test_a_not_scored_company_keeps_its_reason_and_has_no_detail(con):
    view = load_company(con, "BANK")
    assert (view.status, view.reason, view.details, view.flags) == ("not_scored", "financial_or_reit", [], [])


def test_an_unknown_ticker_is_none(con):
    assert load_company(con, "ZZZ") is None


def test_annual_data_is_ordered_by_year(con):
    data = load_annual(con, "aaa")
    assert [d["year"] for d in data.dividends] == list(range(2014, 2026))
    assert data.fundamentals[0]["fiscal_year"] == 2015 and data.fundamentals[-1]["fiscal_year"] == 2025
    assert data.year_end_prices[2014] == pytest.approx(20.0)
    assert load_annual(con, "BBB").dividends == []


def test_payments_are_regular_only_and_oldest_first(con):
    payments = load_payments(con, "AAA")
    assert payments[0] == (dt.date(2014, 2, 10), 0.25) and len(payments) == 48   # the special payment is not in the list
    assert payments == sorted(payments)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cache_company.py`
Expected: FAIL with `ImportError: cannot import name 'load_company' from 'dgi.cache'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/cache/company.py`:
```python
"""One company's read-only data: metrics, score explanation, flags, and the annual series behind the charts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from dgi.scoring.config import PILLARS


@dataclass(frozen=True)
class CompanyView:
    ticker: str
    name: str
    sic: str | None
    sic_description: str | None
    sector_group: str | None
    status: str
    reason: str | None
    score: dict[str, Any]
    metrics: dict[str, Any]
    details: list[dict[str, Any]]
    flags: list[dict[str, Any]]


@dataclass(frozen=True)
class AnnualData:
    dividends: list[dict[str, Any]]
    fundamentals: list[dict[str, Any]]
    year_end_prices: dict[int, float]


def _records(con: duckdb.DuckDBPyConnection, sql: str, values: list[Any]) -> list[dict[str, Any]]:
    cur = con.execute(sql, values)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def load_company(con: duckdb.DuckDBPyConnection, ticker: str) -> CompanyView | None:
    """None when the ticker is not in the cache. Matching ignores case."""
    ticker = ticker.upper()
    company = _records(con, "SELECT * FROM company_dim WHERE ticker = ?", [ticker])
    if not company:
        return None
    score = (_records(con, "SELECT * FROM scores WHERE ticker = ?", [ticker]) or [{}])[0]
    metrics = (_records(con, "SELECT * FROM metrics_current WHERE ticker = ?", [ticker]) or [{}])[0]
    order = " ".join(f"WHEN '{p}' THEN {i}" for i, p in enumerate(PILLARS))
    details = _records(con, f"SELECT * FROM score_detail WHERE ticker = ? ORDER BY CASE pillar {order} END, contribution DESC", [ticker])
    flags = _records(con, "SELECT * FROM flags WHERE ticker = ? ORDER BY CASE severity WHEN 'red' THEN 0 ELSE 1 END, code", [ticker])
    c = company[0]
    return CompanyView(
        ticker=c["ticker"], name=c["name"], sic=c["sic"], sic_description=c["sic_description"],
        sector_group=score.get("sector_group"), status=score.get("status", "not_scored"), reason=score.get("reason"),
        score=score, metrics=metrics, details=details, flags=flags,
    )


def load_annual(con: duckdb.DuckDBPyConnection, ticker: str) -> AnnualData:
    ticker = ticker.upper()
    dividends = _records(con, "SELECT year, dps, n_payments, special_total, complete FROM dividend_annual WHERE ticker = ? ORDER BY year", [ticker])
    fundamentals = _records(con, "SELECT * FROM fundamentals_annual WHERE ticker = ? ORDER BY fiscal_year", [ticker])
    prices = {y: p for y, p in con.execute("SELECT year, close_adj FROM price_yearend WHERE ticker = ? ORDER BY year", [ticker]).fetchall()}
    return AnnualData(dividends, fundamentals, prices)


def load_payments(con: duckdb.DuckDBPyConnection, ticker: str) -> list[tuple[date, float]]:
    """Split-adjusted regular (non-special) payments, oldest first: the input of the daily yield series."""
    rows = con.execute(
        "SELECT ex_date, amount_adj FROM dividend_payment WHERE ticker = ? AND NOT is_special ORDER BY ex_date", [ticker.upper()]
    ).fetchall()
    return [(d, a) for d, a in rows]
```

`src/dgi/cache/__init__.py` (final version):
```python
"""The derived cache: meta and refresh planning, atomic build and swap, quality checks, read-only queries.

The web app imports only this package; the names it needs from `scoring` are re-exported here.
"""

from dgi.cache.company import AnnualData, CompanyView, load_annual, load_company, load_payments
from dgi.cache.handle import CacheHandle
from dgi.cache.meta import CacheMeta, read_meta_from
from dgi.cache.screener import (
    BadQuery, ScatterPoint, ScreenerPage, ScreenerQuery, ScreenerRow, UniverseStats, UnscoredRow, export_rows, parse_query,
    query_screener, query_unscored, scatter_points, sector_groups, universe_stats,
)
from dgi.cache.status import CacheStatus, read_status_from
from dgi.scoring.config import PILLARS, HardFilters, ScoringConfig, load_scoring_config
from dgi.scoring.valuation import FairValue, fair_value_range, grid_axes, sensitivity_grid

__all__ = [
    "AnnualData", "BadQuery", "CacheHandle", "CacheMeta", "CacheStatus", "CompanyView", "FairValue", "HardFilters", "PILLARS", "ScatterPoint",
    "ScoringConfig", "ScreenerPage", "ScreenerQuery", "ScreenerRow", "UniverseStats", "UnscoredRow", "export_rows",
    "fair_value_range", "grid_axes", "load_annual", "load_company", "load_payments", "load_scoring_config", "parse_query",
    "query_screener", "query_unscored", "read_meta_from", "read_status_from", "scatter_points", "sector_groups", "sensitivity_grid", "universe_stats",
]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cache_company.py tests/test_cache_handle.py tests/test_cache_screener.py`
Expected: PASS (6 + 5 + 35 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/cache tests/test_cache_company.py
git commit -m "feat: company queries over the cache"
```

---

### Task 20: Web support modules (formatting, chart data, vendored ECharts)

Pure modules the app builds on. No routes yet.

**Files:**
- Create: `src/dgi/web/__init__.py`, `src/dgi/web/format.py`, `src/dgi/web/series.py`, `scripts/vendor_echarts.sh`, `src/dgi/web/static/vendor/` (vendored files, committed)
- Test: `tests/test_web_format.py`, `tests/test_web_series.py`

**Interfaces:**
- Consumes: `dgi.cache.AnnualData`.
- Produces:
  - `format`: `pct(value, digits=1)`, `num(value, digits=1)`, `money(value, digits=2)`, `big(value)`, `score(value)`, `metric_label(name)`, `metric_value(name, value)`, `reason_label(reason)`, `streak_label(streak, years_history)` (adds a plus when the streak reaches back to the start of the available history, so the real record may be longer: KO and JNJ show `55+` because Yahoo's dividend history starts around 1970), `csv_safe(text)` (prefixes `'` to text starting with `=`, `+`, `-` or `@`), constants `DASH`, `PILLAR_LABELS`. None renders as `–`.
  - `series`: `ChartSeries(name, values, slot)`, `ChartSpec(id, title, kind, unit, x, series, x_type="category", note=None, references=[])` with `to_json()`; `annual_charts(data: AnnualData, average_yield) -> list[ChartSpec]` (order: dividend bars, yield, growth, payout, per share, leverage, coverage, shares; charts without data are left out); `dividend_charts`, `yield_chart`, `fundamentals_charts`; `trailing_yield(points, payments) -> list[float | None]` (previous 365 days of regular payments over the close; None within a year of the first payment); `daily_charts(points, payments, average_yield)`; protocol `PriceSource.daily(ticker) -> list[tuple[date, float]]`; `SeriesCache.get_or_load(upstream_key, ticker, load)` (keyed by upstream key and ticker, drops entries of any other upstream key, evicts the oldest past 64 entries).

- [ ] **Step 1: Write the failing tests**

`tests/test_web_format.py` (UNIT: formatting helpers):
```python
import pytest

from dgi.web.format import big, csv_safe, metric_label, metric_value, money, num, pct, reason_label, score, streak_label


def test_none_shows_as_a_dash_everywhere():
    assert [f(None) for f in (pct, num, money, big, score)] == ["–"] * 5
    assert metric_value("streak", None) == "–"


def test_numbers():
    assert pct(0.0345) == "3.5%" and pct(0.0345, 0) == "3%" and pct(-0.1) == "-10.0%"
    assert num(1234.567) == "1,234.6" and num(1234.567, 0) == "1,235"
    assert money(60) == "$60.00" and money(1234.4, 0) == "$1,234"
    assert score(86.6) == "87"


@pytest.mark.parametrize("value, expected", [(5e12, "$5.0T"), (2.5e9, "$2.5B"), (7.2e6, "$7.2M"), (950.0, "$950")])
def test_big_picks_a_unit(value, expected):
    assert big(value) == expected


def test_metric_values_follow_their_kind():
    assert metric_value("dgr_5", 0.0512) == "5.1%"
    assert metric_value("streak", 11.0) == "11"
    assert metric_value("pe", 18.456) == "18.46"
    assert metric_value("payout_fcf", 9.99) == "999.0%"


def test_labels():
    assert metric_label("dgr_5") == "Dividend growth, 5-year CAGR" and metric_label("unknown") == "unknown"
    assert "REIT" in reason_label("financial_or_reit") and reason_label(None) == "–" and reason_label("odd") == "odd"


@pytest.mark.parametrize("text, expected", [("=SUM(A1)", "'=SUM(A1)"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"), ("Coca-Cola", "Coca-Cola"), (None, ""), (5, "5")])
def test_csv_safe_blocks_formula_injection(text, expected):
    assert csv_safe(text) == expected


@pytest.mark.parametrize("streak, history, expected", [(30, 45, "30"), (55, 56, "55+"), (12, 13, "12+"), (0, 1, "0"), (None, 10, "–"), (5, None, "5")])
def test_streak_label_adds_a_plus_when_the_record_reaches_the_start_of_the_history(streak, history, expected):
    assert streak_label(streak, history) == expected
```

`tests/test_web_series.py` (UNIT: chart data builders, trailing yield, SeriesCache):
```python
import datetime as dt

import pytest

from dgi.cache import AnnualData
from dgi.web.series import (
    ChartSeries, ChartSpec, SeriesCache, annual_charts, daily_charts, dividend_charts, fundamentals_charts, trailing_yield, yield_chart,
)

D = dt.date


def annual(**over):
    dividends = [{"year": y, "dps": 1.0 + 0.1 * (y - 2020), "n_payments": 4, "special_total": 0.0, "complete": y < 2026} for y in range(2020, 2027)]
    fundamentals = [
        {"fiscal_year": y, "eps_diluted": 3.0, "fcf_per_share": 3.5, "shares_diluted": 100e6 - 1e6 * (y - 2020), "operating_income": 200.0,
         "interest_expense": 10.0, "net_debt": 300.0, "ebitda": 250.0, "payout_earnings": 0.5, "payout_fcf": 0.45}
        for y in range(2021, 2026)
    ]
    return AnnualData(over.get("dividends", dividends), over.get("fundamentals", fundamentals),
                      over.get("prices", {y: 20.0 + y - 2020 for y in range(2020, 2026)}))


def by_id(charts):
    return {c.id: c for c in charts}


def test_dividend_charts_use_complete_years_and_compute_growth():
    charts = by_id(dividend_charts(annual()))
    assert charts["dps"].x == [2020, 2021, 2022, 2023, 2024, 2025] and charts["dps"].kind == "bar"
    growth = charts["dps_growth"].series[0].values
    assert growth[0] is None and growth[1] == pytest.approx(0.1) and growth[2] == pytest.approx(1.2 / 1.1 - 1)


def test_no_dividends_means_no_dividend_charts():
    assert dividend_charts(annual(dividends=[])) == []
    assert dividend_charts(annual(dividends=[{"year": 2026, "dps": 1.0, "n_payments": 1, "special_total": 0.0, "complete": False}])) == []


def test_yield_chart_divides_by_the_year_end_price_and_carries_the_average():
    chart = yield_chart(annual(), 0.03)
    assert chart.series[0].values[0] == pytest.approx(1.0 / 20.0) and chart.references == [{"name": "5-year average", "value": 0.03}]
    assert yield_chart(annual(), None).references == []
    assert yield_chart(annual(prices={}), 0.03) is None


def test_payout_values_above_200_percent_are_drawn_at_200_with_a_note():
    fundamentals = [{"fiscal_year": 2025, "eps_diluted": None, "fcf_per_share": None, "shares_diluted": None, "operating_income": None,
                     "interest_expense": None, "net_debt": None, "ebitda": None, "payout_earnings": 9.99, "payout_fcf": 0.8}]
    chart = by_id(fundamentals_charts(annual(fundamentals=fundamentals)))["payout"]
    assert chart.series[0].values == [2.0] and chart.series[1].values == [0.8]
    assert "above 200%" in chart.note and chart.references == [{"name": "100%", "value": 1.0}]


def test_leverage_coverage_and_shares_edge_cases():
    base = {"fiscal_year": 2025, "eps_diluted": 1.0, "fcf_per_share": 1.0, "shares_diluted": 50e6, "payout_earnings": None, "payout_fcf": None,
            "operating_income": 100.0, "interest_expense": 0.0, "net_debt": -50.0, "ebitda": 80.0}
    rows = [base, {**base, "fiscal_year": 2026, "interest_expense": 5.0, "net_debt": 100.0, "ebitda": -10.0},
            {**base, "fiscal_year": 2027, "operating_income": None, "net_debt": None}]
    charts = by_id(fundamentals_charts(annual(fundamentals=rows)))
    assert charts["leverage"].series[0].values == [0.0, 10.0, None]          # net cash, negative EBITDA drawn at the cap, unknown
    assert charts["coverage"].series[0].values == [50.0, 20.0, None]          # no interest expense draws at the cap
    assert charts["shares"].series[0].values == [50.0, 50.0, 50.0]


def test_charts_without_any_data_are_left_out():
    empty = [{"fiscal_year": 2025, **{k: None for k in ("eps_diluted", "fcf_per_share", "shares_diluted", "operating_income", "interest_expense", "net_debt", "ebitda", "payout_earnings", "payout_fcf")}}]
    assert fundamentals_charts(annual(fundamentals=empty)) == []
    assert fundamentals_charts(annual(fundamentals=[])) == []


def test_per_share_chart_matches_dividends_to_fiscal_years():
    chart = by_id(fundamentals_charts(annual()))["per_share"]
    assert [s.name for s in chart.series] == ["EPS", "Free cash flow per share", "Dividend per share"]
    assert [s.slot for s in chart.series] == [1, 2, 3]
    assert chart.series[2].values == [pytest.approx(1.1), pytest.approx(1.2), pytest.approx(1.3), pytest.approx(1.4), pytest.approx(1.5)]


def test_annual_charts_order_puts_the_yield_after_the_dividend_bars():
    ids = [c.id for c in annual_charts(annual(), 0.03)]
    assert ids[:3] == ["dps", "yield", "dps_growth"] and set(ids) >= {"payout", "per_share", "leverage", "coverage", "shares"}


def test_to_json_is_plain_data():
    spec = ChartSpec("x", "T", "line", "pct", [1, 2], [ChartSeries("S", [0.1, None], 2)], note="n", references=[{"name": "r", "value": 1.0}])
    assert spec.to_json() == {"id": "x", "title": "T", "kind": "line", "unit": "pct", "x": [1, 2], "xType": "category",
                              "series": [{"name": "S", "values": [0.1, None], "slot": 2}], "note": "n", "references": [{"name": "r", "value": 1.0}]}


# daily -----------------------------------------------------------------------------------------------------------------

PAYMENTS = [(D(2023, 3, 1), 0.25), (D(2023, 6, 1), 0.25), (D(2023, 9, 1), 0.25), (D(2023, 12, 1), 0.25), (D(2024, 3, 1), 0.30)]


def test_trailing_yield_sums_the_previous_365_days_and_waits_a_year_for_the_first_payment():
    points = [(D(2023, 12, 15), 50.0), (D(2024, 3, 2), 50.0), (D(2024, 3, 2) + dt.timedelta(days=365), 50.0), (D(2024, 6, 1), 0.0)]
    values = trailing_yield(points, PAYMENTS)
    assert values[0] is None                                    # less than a year after the first payment
    assert values[1] == pytest.approx((0.25 * 3 + 0.30) / 50.0)  # 2023-06, 09, 12 and 2024-03 (2023-03-01 is just outside the window)
    assert values[2] is None                                    # a year on, the last payment has left the window
    assert values[3] is None                                    # zero price


def test_trailing_yield_with_no_payments_is_all_none():
    assert trailing_yield([(D(2024, 1, 1), 10.0)], []) == [None]


def test_daily_charts_have_price_and_yield_with_the_average_line():
    points = [(D(2024, 4, 1) + dt.timedelta(days=i), 40.0 + i) for i in range(5)]
    charts = by_id(daily_charts(points, PAYMENTS, 0.03))
    assert charts["price"].x_type == "time" and charts["price"].x[0] == "2024-04-01" and charts["price"].series[0].values[0] == 40.0
    assert charts["yield_daily"].references == [{"name": "5-year average", "value": 0.03}]
    assert daily_charts([], PAYMENTS, None) == []
    assert "yield_daily" not in by_id(daily_charts(points, [], None))


# cache -----------------------------------------------------------------------------------------------------------------

def test_series_cache_loads_once_per_key_and_never_serves_another_upstream():
    cache, calls = SeriesCache(), []

    def loader(tag):
        def load():
            calls.append(tag)
            return [(D(2024, 1, 1), 1.0)]
        return load

    cache.get_or_load("u1", "KO", loader("a"))
    cache.get_or_load("u1", "KO", loader("b"))
    assert calls == ["a"]
    cache.get_or_load("u2", "KO", loader("c"))      # a new upstream key reloads and drops the old entries
    cache.get_or_load("u1", "KO", loader("d"))
    assert calls == ["a", "c", "d"]


def test_series_cache_evicts_the_oldest_entry_past_its_limit():
    cache, calls = SeriesCache(max_entries=2), []
    for ticker in ("A", "B", "C"):
        cache.get_or_load("u", ticker, lambda t=ticker: calls.append(t) or [])
    cache.get_or_load("u", "A", lambda: calls.append("A again") or [])
    assert calls == ["A", "B", "C", "A again"]


def test_a_failing_loader_caches_nothing():
    cache = SeriesCache()
    with pytest.raises(RuntimeError):
        cache.get_or_load("u", "A", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert cache.get_or_load("u", "A", lambda: [(D(2024, 1, 1), 2.0)]) == [(D(2024, 1, 1), 2.0)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_web_format.py tests/test_web_series.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.web'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/web/__init__.py`:
```python
"""The read-only web UI: Starlette routes over the cache, Jinja templates, ECharts."""
```

`src/dgi/web/format.py`:
```python
"""Number and metric formatting for templates. Pure functions; None shows as a dash."""

from __future__ import annotations

from typing import Any

DASH = "–"

METRIC_LABELS = {
    "streak": "Years of dividend increases", "no_cut_streak": "Years without a cut", "dgr_1": "Dividend growth, 1 year",
    "dgr_3": "Dividend growth, 3-year CAGR", "dgr_5": "Dividend growth, 5-year CAGR", "dgr_10": "Dividend growth, 10-year CAGR",
    "yield_vs_avg": "Yield vs its 5-year average", "payment_frequency": "Payments per year",
    "payout_earnings": "Payout of earnings", "payout_earnings_5y": "Payout of earnings, 5-year", "payout_fcf": "Payout of free cash flow",
    "payout_fcf_5y": "Payout of free cash flow, 5-year", "interest_coverage": "Interest coverage", "net_debt_ebitda": "Net debt / EBITDA",
    "current_ratio": "Current ratio", "positive_earnings_years": "Profitable years in the last 10",
    "rev_cagr_5": "Revenue growth, 5-year CAGR", "eps_cagr_5": "EPS growth, 5-year CAGR", "fcf_ps_cagr_5": "FCF per share growth, 5-year CAGR",
    "roe": "Return on equity", "op_margin_std": "Operating margin volatility", "share_trend_5": "Share count change, 5-year CAGR",
    "pe": "P/E", "p_fcf": "P/FCF", "fcf_yield": "FCF yield", "margin_of_safety": "Margin of safety vs fair value",
}
PERCENT_METRICS = {
    "dgr_1", "dgr_3", "dgr_5", "dgr_10", "yield_vs_avg", "payout_earnings", "payout_earnings_5y", "payout_fcf", "payout_fcf_5y",
    "rev_cagr_5", "eps_cagr_5", "fcf_ps_cagr_5", "roe", "op_margin_std", "share_trend_5", "fcf_yield", "margin_of_safety",
}
INTEGER_METRICS = {"streak", "no_cut_streak", "payment_frequency", "positive_earnings_years"}
PILLAR_LABELS = {"dividend": "Dividend record", "safety": "Safety", "growth": "Growth and quality", "valuation": "Valuation"}
REASON_LABELS = {
    "financial_or_reit": "Bank, insurer or REIT: payout and coverage ratios do not describe these businesses, so they are not scored in v1",
    "short_dividend_history": "Fewer complete years of dividend history than the minimum",
    "no_price": "No recent price",
    "insufficient_data": "Too little data to score reliably",
}


def pct(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else f"{value * 100:.{digits}f}%"


def num(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else f"{value:,.{digits}f}"


def money(value: float | None, digits: int = 2) -> str:
    return DASH if value is None else f"${value:,.{digits}f}"


def big(value: float | None) -> str:
    if value is None:
        return DASH
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= limit:
            return f"${value / limit:,.1f}{suffix}"
    return f"${value:,.0f}"


def score(value: float | None) -> str:
    return DASH if value is None else f"{value:.0f}"


def streak_label(value: float | None, years_history: float | None) -> str:
    """The streak, with a plus when it reaches back to the start of the available dividend history (the real record may be longer)."""
    if value is None:
        return DASH
    capped = value > 0 and years_history is not None and value >= years_history - 1
    return f"{value:.0f}+" if capped else f"{value:.0f}"


def metric_label(name: str) -> str:
    return METRIC_LABELS.get(name, name)


def metric_value(name: str, value: float | None) -> str:
    if value is None:
        return DASH
    if name in PERCENT_METRICS:
        return pct(value)
    if name in INTEGER_METRICS:
        return f"{value:.0f}"
    return num(value, 2)


def reason_label(reason: str | None) -> str:
    return REASON_LABELS.get(reason or "", reason or DASH)


def csv_safe(text: Any) -> str:
    """Stop a spreadsheet from running a cell: text starting with = + - @ gets a leading apostrophe."""
    value = "" if text is None else str(text)
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value
```

`src/dgi/web/series.py`:
```python
"""Chart data for the company page: pure builders over cache rows, plus the daily-price cache."""

from __future__ import annotations

import threading
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import accumulate
from typing import Any, Protocol

from dgi.cache import AnnualData

PAYOUT_DISPLAY_CAP = 2.0      # payout ratios above 200% are drawn at 200% (the table keeps the real value)
COVERAGE_DISPLAY_CAP = 50.0
LEVERAGE_DISPLAY_CAP = 10.0


@dataclass(frozen=True)
class ChartSeries:
    name: str
    values: list[float | None]
    slot: int  # categorical colour slot 1..3, assigned by series order and never by rank


@dataclass(frozen=True)
class ChartSpec:
    id: str
    title: str
    kind: str                       # "bar" | "line"
    unit: str                       # "usd" | "pct" | "x" | "millions"
    x: list[Any]
    series: list[ChartSeries]
    x_type: str = "category"        # "category" (years) | "time" (ISO dates)
    note: str | None = None
    references: list[dict[str, Any]] = field(default_factory=list)  # [{"name": "100%", "value": 1.0}]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "unit": self.unit, "x": self.x, "xType": self.x_type,
            "series": [{"name": s.name, "values": s.values, "slot": s.slot} for s in self.series],
            "note": self.note, "references": self.references,
        }


def _present(values: Sequence[float | None]) -> bool:
    return any(v is not None for v in values)


def _years(rows: Sequence[Mapping[str, Any]], key: str) -> list[int]:
    return [r[key] for r in rows]


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _clamp(value: float | None, cap: float) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    return (cap, True) if value > cap else (value, False)


def dividend_charts(data: AnnualData) -> list[ChartSpec]:
    rows = [r for r in data.dividends if r["complete"] and r["dps"] is not None]
    if not rows:
        return []
    years = _years(rows, "year")
    dps = [r["dps"] for r in rows]
    growth = [None] + [(b / a - 1) if a > 0 else None for a, b in zip(dps, dps[1:])]
    charts = [ChartSpec("dps", "Dividend per share by year", "bar", "usd", years, [ChartSeries("Dividend per share", dps, 1)],
                        note="Split-adjusted regular payments of each complete calendar year; special dividends are excluded.")]
    if _present(growth):
        charts.append(ChartSpec("dps_growth", "Dividend growth, year over year", "line", "pct", years, [ChartSeries("Growth", growth, 1)]))
    return charts


def yield_chart(data: AnnualData, average: float | None) -> ChartSpec | None:
    rows = [r for r in data.dividends if r["complete"] and r["dps"] is not None and r["year"] in data.year_end_prices]
    if not rows:
        return None
    values = [_ratio(r["dps"], data.year_end_prices[r["year"]]) for r in rows]
    refs = [] if average is None else [{"name": "5-year average", "value": average}]
    return ChartSpec("yield", "Dividend yield at each year end", "line", "pct", _years(rows, "year"), [ChartSeries("Yield", values, 1)],
                     note="Annual dividend per share over the year-end close, both on today's share basis.", references=refs)


def fundamentals_charts(data: AnnualData) -> list[ChartSpec]:
    rows = data.fundamentals
    if not rows:
        return []
    years = _years(rows, "fiscal_year")
    charts: list[ChartSpec] = []
    clamped = False
    payout_series = []
    for name, key, slot in (("Of earnings", "payout_earnings", 1), ("Of free cash flow", "payout_fcf", 2)):
        values = []
        for r in rows:
            value, was_clamped = _clamp(r[key], PAYOUT_DISPLAY_CAP)
            clamped = clamped or was_clamped
            values.append(value)
        payout_series.append(ChartSeries(name, values, slot))
    if any(_present(s.values) for s in payout_series):
        note = "Cash dividends paid over net income and over free cash flow. Values above 200% are drawn at 200%." if clamped else \
            "Cash dividends paid over net income and over free cash flow."
        charts.append(ChartSpec("payout", "Payout ratios", "line", "pct", years, payout_series, note=note, references=[{"name": "100%", "value": 1.0}]))
    dps_by_year = {r["year"]: r["dps"] for r in data.dividends}
    per_share = [
        ChartSeries("EPS", [r["eps_diluted"] for r in rows], 1),
        ChartSeries("Free cash flow per share", [r["fcf_per_share"] for r in rows], 2),
        ChartSeries("Dividend per share", [dps_by_year.get(r["fiscal_year"]) for r in rows], 3),
    ]
    if _present(per_share[0].values) or _present(per_share[1].values):
        charts.append(ChartSpec("per_share", "EPS, free cash flow and dividend per share", "line", "usd", years, per_share,
                                note="Per-share figures are restated to today's share basis; dividends are matched by calendar year."))
    leverage = [_ratio_or_none(r["net_debt"], r["ebitda"], LEVERAGE_DISPLAY_CAP) for r in rows]
    if _present(leverage):
        charts.append(ChartSpec("leverage", "Net debt / EBITDA", "line", "x", years, [ChartSeries("Net debt / EBITDA", leverage, 1)],
                                note="Lower is safer. Net cash shows as zero; values above 10 are drawn at 10."))
    coverage = [_coverage(r["operating_income"], r["interest_expense"]) for r in rows]
    if _present(coverage):
        charts.append(ChartSpec("coverage", "Interest coverage", "line", "x", years, [ChartSeries("Operating income / interest", coverage, 1)],
                                note="Higher is safer. Values above 50 are drawn at 50; companies with no interest expense show 50."))
    shares = [None if r["shares_diluted"] is None else r["shares_diluted"] / 1e6 for r in rows]
    if _present(shares):
        charts.append(ChartSpec("shares", "Diluted share count", "line", "millions", years, [ChartSeries("Shares (millions)", shares, 1)],
                                note="Falling means buybacks. Restated to today's share basis."))
    return charts


def _ratio_or_none(net_debt: float | None, ebitda: float | None, cap: float) -> float | None:
    if net_debt is None or ebitda is None:
        return None
    if net_debt <= 0:
        return 0.0
    return cap if ebitda <= 0 else min(net_debt / ebitda, cap)


def _coverage(operating_income: float | None, interest_expense: float | None) -> float | None:
    if operating_income is None:
        return None
    if interest_expense is None or interest_expense <= 0:
        return COVERAGE_DISPLAY_CAP
    return min(operating_income / interest_expense, COVERAGE_DISPLAY_CAP)


def annual_charts(data: AnnualData, average_yield: float | None) -> list[ChartSpec]:
    """All the company page's charts that come from the cache. Charts without any data are left out."""
    charts = dividend_charts(data)
    annual_yield = yield_chart(data, average_yield)
    if annual_yield:
        charts.insert(1, annual_yield)
    return charts + fundamentals_charts(data)


# daily series (drill-down) -------------------------------------------------------------------------------------------

DailyPoints = Sequence[tuple[date, float]]


class PriceSource(Protocol):
    def daily(self, ticker: str) -> list[tuple[date, float]]:
        """Split-adjusted daily closes, oldest first. Raises a DgiError when the API cannot answer."""


def trailing_yield(points: DailyPoints, payments: Sequence[tuple[date, float]]) -> list[float | None]:
    """Yield on each day: regular payments of the previous 365 days over that day's split-adjusted close.

    Days within a year of the first payment are None, because the trailing window would be incomplete.
    """
    if not payments:
        return [None] * len(points)
    dates = [d for d, _ in payments]
    prefix = [0.0, *accumulate(a for _, a in payments)]
    first = dates[0] + timedelta(days=365)
    values: list[float | None] = []
    for day, close in points:
        if close <= 0 or day < first:
            values.append(None)
            continue
        high, low = bisect_right(dates, day), bisect_right(dates, day - timedelta(days=365))
        total = prefix[high] - prefix[low]
        values.append(total / close if total > 0 else None)
    return values


def daily_charts(points: DailyPoints, payments: Sequence[tuple[date, float]], average_yield: float | None) -> list[ChartSpec]:
    if not points:
        return []
    days = [d.isoformat() for d, _ in points]
    price = ChartSpec("price", "Share price (split-adjusted)", "line", "usd", days, [ChartSeries("Close", [c for _, c in points], 1)], x_type="time")
    refs = [] if average_yield is None else [{"name": "5-year average", "value": average_yield}]
    yields = trailing_yield(points, payments)
    charts = [price]
    if _present(yields):
        charts.append(ChartSpec("yield_daily", "Dividend yield, daily", "line", "pct", days, [ChartSeries("Yield", yields, 1)], x_type="time",
                                note="Regular payments of the previous 365 days over the close.", references=refs))
    return charts


class SeriesCache:
    """Daily series held in memory, keyed by (upstream key, ticker): a new upstream key never sees an old entry."""

    def __init__(self, max_entries: int = 64) -> None:
        self._max = max_entries
        self._items: OrderedDict[tuple[str, str], list[tuple[date, float]]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_load(self, upstream_key: str, ticker: str, load: Callable[[], list[tuple[date, float]]]) -> list[tuple[date, float]]:
        key = (upstream_key, ticker)
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        points = load()
        with self._lock:
            for stale in [k for k in self._items if k[0] != upstream_key]:
                del self._items[stale]
            self._items[key] = points
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return points
```

- [ ] **Step 4: Vendor ECharts**

The script downloads the pinned `echarts` npm tarball and refuses to continue unless its SHA-512 matches the integrity hash the npm registry publishes for that version (network needed once, at implementation time; the UI itself never calls a CDN).

`scripts/vendor_echarts.sh`:
```sh
#!/bin/sh
# Vendors Apache ECharts into the web app so the UI needs no CDN at run time.
# The npm tarball is checked against the integrity hash the npm registry publishes for that version.
#   scripts/vendor_echarts.sh            # ECHARTS_VERSION defaults to the pinned version below
#   ECHARTS_VERSION=5.6.0 DGI_VENDOR_DEST=/some/dir scripts/vendor_echarts.sh
set -eu

VERSION="${ECHARTS_VERSION:-5.6.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${DGI_VENDOR_DEST:-$ROOT/src/dgi/web/static/vendor}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

json_field() {  # json_field FILE dist.key
  uv run --no-project python -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["dist"][sys.argv[2]])' "$1" "$2"
}

curl -fsSL "https://registry.npmjs.org/echarts/$VERSION" -o "$TMP/meta.json"
TARBALL="$(json_field "$TMP/meta.json" tarball)"
EXPECTED="$(json_field "$TMP/meta.json" integrity)"
curl -fsSL "$TARBALL" -o "$TMP/echarts.tgz"
ACTUAL="sha512-$(openssl dgst -sha512 -binary "$TMP/echarts.tgz" | openssl base64 -A)"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "integrity mismatch for echarts@$VERSION: expected $EXPECTED, got $ACTUAL" >&2
  exit 1
fi
tar -xzf "$TMP/echarts.tgz" -C "$TMP" package/dist/echarts.min.js package/LICENSE package/NOTICE
mkdir -p "$DEST"
cp "$TMP/package/dist/echarts.min.js" "$DEST/echarts.min.js"
cp "$TMP/package/LICENSE" "$DEST/ECHARTS_LICENSE"
cp "$TMP/package/NOTICE" "$DEST/ECHARTS_NOTICE"
echo "$VERSION" > "$DEST/ECHARTS_VERSION"
shasum -a 256 "$DEST/echarts.min.js" | awk '{print $1}' > "$DEST/echarts.min.js.sha256"
echo "vendored echarts@$VERSION into $DEST"
```

```bash
chmod +x scripts/vendor_echarts.sh
scripts/vendor_echarts.sh
ls -la src/dgi/web/static/vendor
```
Expected: `vendored echarts@5.6.0 into .../static/vendor`; the directory holds `echarts.min.js` (about 1 MB), `ECHARTS_LICENSE`, `ECHARTS_NOTICE`, `ECHARTS_VERSION` and `echarts.min.js.sha256`. These files are committed (the image is built from the repo, offline).

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_web_format.py tests/test_web_series.py tests/test_architecture.py`
Expected: PASS (21 + 15 tests; the architecture test now checks `web`, which imports nothing from `dgi` except `dgi.cache`).

- [ ] **Step 6: Commit**

```bash
git add src/dgi/web scripts/vendor_echarts.sh tests/test_web_format.py tests/test_web_series.py
git commit -m "feat: web formatting, chart data builders and vendored ECharts"
```

---

### Task 21: Web app (routes, templates, styles, charts)

One task with three checkpoints, each ending green and committed: (A) app skeleton with health, methodology and the no-cache page; (B) the screener; (C) the company page. The route handlers stay thin: parse, query `dgi.cache`, render.

**Files:**
- Create: `src/dgi/web/app.py`, `src/dgi/web/templates/{base,no_cache,error,screener,company,methodology}.html`, `src/dgi/web/static/app.css`, `src/dgi/web/static/charts.js`
- Test: `tests/test_web_app.py`, `tests/test_web_screener.py`, `tests/test_web_company.py`

**Interfaces:**
- Consumes: Tasks 18-20, `Settings`, `ScoringConfig`.
- Produces:
  - `create_app(settings: Settings, cfg: ScoringConfig, price_source: PriceSource | None = None) -> Starlette`. Routes: `GET /` (screener; the `unscored=1` view lists companies that are not scored with their reason), `GET /screener.csv`, `GET /api/scatter`, `GET /company/{ticker}`, `GET /api/company/{ticker}/daily`, `GET /methodology`, `GET /health` (always 200; `{"status": "no_cache"}` before the first refresh, so readiness probes pass), `/static/*`. Without a cache the HTML pages answer 503 with a "run `dgi refresh`" page. Responses over 1 KB are gzip-compressed (a long company's daily series is close to 1 MB of JSON). A bad query parameter answers 422 naming the parameter; an unknown route, an unknown ticker or a ticker that does not match `[A-Za-z0-9.\-]{1,10}` answers 404. Lookups ignore ticker case.
  - Page data: the screener shows the universe strip, a filter bar filled from the URL and the config defaults, pillar-weight sliders, a yield-versus-5-year-growth scatter (size = score, click opens the company), a sortable, paged table with warning badges, and CSV export (company names starting with a formula character are neutralized). The company page shows header and score badge, flags in words, pillar bars, KPI tiles, fair value and margin of safety, charts with a table disclosure each, the valuation panel with the sensitivity grid, and "Why this score" (value, band score, weight, points of total, percentile). A company that is not scored shows its reason and whatever history exists, with no score.
  - Daily drill-down: `/api/company/{ticker}/daily` returns `{"available": true, "charts": [...]}` (price and daily yield), or `{"available": false, "notice": ...}` when there is no source or the API fails; the page then keeps the annual charts and shows the notice. Series are cached in memory by (upstream key, ticker).

**Checkpoint A: skeleton**

- [ ] **Step A1: Write the failing tests**

`tests/test_web_app.py` (UNIT: no-cache pages, health, methodology, static assets, 404s, footer, cache swap):
```python
import json

import pytest
from starlette.testclient import TestClient

from dgi.cache.build import new_path, swap_in
from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG, cache_with, make_meta, persist
from tests.web_fixtures import sample_cache


def client_for(tmp_path, price_source=None) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path), CFG, price_source))


@pytest.fixture
def web(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with client_for(tmp_path) as client:
        yield client


def test_without_a_cache_pages_say_to_run_refresh_and_health_still_answers(tmp_path):
    with client_for(tmp_path) as client:
        for path in ("/", "/company/AAA", "/screener.csv"):
            response = client.get(path)
            assert response.status_code == 503 and "dgi refresh" in response.text
        assert client.get("/health").json() == {"status": "no_cache"}
        assert client.get("/api/scatter").status_code == 503
        assert client.get("/api/company/AAA/daily").status_code == 503


def test_health_reports_the_cache(web):
    body = web.get("/health").json()
    assert body["status"] == "ok" and body["upstream_key"] == make_meta().upstream_key and body["contract_version"] == "v1"
    assert body["counts"]["company_dim"] == 7 and body["scored"] == 5
    assert body["not_scored"] == {"financial_or_reit": 1, "short_dividend_history": 1}


def test_methodology_shows_the_active_configuration_with_or_without_a_cache(web, tmp_path):
    text = web.get("/methodology").text
    assert "Pillar weights" in text and "Dividend record" in text and "30%" in text
    assert "Years of dividend increases" in text and "6798" in text and "Gordon growth" in text
    with client_for(tmp_path / "nowhere") as bare:
        assert bare.get("/methodology").status_code == 200


def test_static_assets_including_the_vendored_charting_library_are_served(web):
    for path in ("/static/app.css", "/static/charts.js", "/static/vendor/echarts.min.js"):
        assert web.get(path).status_code == 200, path
    assert len(web.get("/static/vendor/echarts.min.js").content) > 500_000


def test_unknown_addresses_and_malformed_tickers_are_a_404_page_not_an_error(web):
    for path in ("/nope", "/company/ZZZ", "/company/bad;ticker", "/company/AA%2FA", "/company/" + "A" * 11, "/api/company/ZZZ/daily"):
        response = web.get(path)
        assert response.status_code == 404, path
    assert "nothing at this address" in web.get("/nope").text


def test_the_footer_shows_the_build_and_any_contract_warning(tmp_path):
    persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb", make_meta(contract_warnings="contract v1 is deprecated, sunset 2027-01-01"))
    with client_for(tmp_path) as client:
        text = client.get("/methodology").text
    assert "Cache built 2026-10-05 07:00 UTC" in text and "contract v1" in text and "deprecated, sunset 2027-01-01" in text


def test_a_cache_swapped_under_the_running_app_is_served_without_a_restart(tmp_path):
    live = persist(cache_with({"OLD": ("2080", {})}), tmp_path / "dgi.duckdb")
    with client_for(tmp_path) as client:
        assert client.get("/health").json()["counts"]["company_dim"] == 1
        swap_in(persist(cache_with({"A": ("2080", {}), "B": ("2080", {}), "C": ("2080", {})}), new_path(live)), live)
        assert client.get("/health").json()["counts"]["company_dim"] == 3
```

- [ ] **Step A2: Run to verify it fails**

Run: `uv run pytest tests/test_web_app.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.web.app'`.

- [ ] **Step A3: Write the app, the shared templates and the styles**

The theme tokens in `app.css` come from the dataviz reference palette: light and dark are their own selected steps, declared for both the OS setting and the manual `data-theme` toggle.

`src/dgi/web/app.py`:
```python
"""Routes of the read-only UI. Handlers are thin: parse, query the cache, render. All logic lives in `dgi.cache` and `dgi.web.series`."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from dgi.cache import (
    BadQuery, CacheHandle, CacheMeta, ScoringConfig, export_rows, fair_value_range, grid_axes, load_annual, load_company, load_payments,
    parse_query, query_screener, query_unscored, read_meta_from, read_status_from, scatter_points, sector_groups, sensitivity_grid,
    universe_stats, PILLARS,
)
from dgi.errors import DgiError
from dgi.settings import Settings
from dgi.web import format as fmt
from dgi.web.series import PriceSource, SeriesCache, annual_charts, daily_charts

HERE = Path(__file__).parent
TICKER = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")
CSV_COLUMNS = [
    "rank", "ticker", "name", "sector", "price", "yield_pct", "streak", "dgr_5_pct", "payout_fcf_pct", "payout_eps_pct", "market_cap",
    "dividend_score", "safety_score", "growth_score", "valuation_score", "score", "margin_of_safety_pct", "red_flags",
]


def _times100(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 4)


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    env = templates.env
    env.filters.update(
        pct=fmt.pct, num=fmt.num, money=fmt.money, big=fmt.big, score=fmt.score, metric_label=fmt.metric_label,
        metric_value=fmt.metric_value, reason_label=fmt.reason_label, streak_label=fmt.streak_label,
    )
    env.globals.update(pillar_labels=fmt.PILLAR_LABELS, pillars=PILLARS, dash=fmt.DASH)
    env.globals["qs"] = lambda params, **overrides: urlencode({**params, **{k: str(v) for k, v in overrides.items()}})
    return templates


def footer(meta: CacheMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {"built_at": meta.built_at[:16].replace("T", " "), "upstream_built_at": (meta.upstream_built_at or "unknown")[:10],
            "contract_version": meta.contract_version, "contract_warnings": meta.contract_warnings}


def render(request: Request, name: str, context: dict[str, Any], status: int = 200) -> Response:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def no_cache(request: Request) -> Response:
    return render(request, "no_cache.html", {"footer": None}, status=503)


def not_found(request: Request, exc: Exception) -> Response:
    return render(request, "error.html", {"footer": None, "title": "Not found", "message": "There is nothing at this address."}, status=404)


def bad_query(request: Request, exc: BadQuery) -> Response:
    return render(request, "error.html", {"footer": None, "title": "Check the filters", "message": f"{exc.param}: {exc.reason}"}, status=422)


def screener(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        if con is None:
            return no_cache(request)
        q = parse_query(request.query_params, state.cfg.hard_filters, state.cfg.pillars)
        stats = universe_stats(con, q)
        page, unscored, unscored_total = None, [], 0
        if q.show_unscored:
            unscored, unscored_total = query_unscored(con, q)
        else:
            page = query_screener(con, q)
        context = {"q": q, "stats": stats, "page": page, "unscored": unscored, "unscored_total": unscored_total,
                   "sectors": sector_groups(con), "footer": footer(read_meta_from(con))}
    return render(request, "screener.html", context)


def screener_csv(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        if con is None:
            return no_cache(request)
        rows = export_rows(con, parse_query(request.query_params, state.cfg.hard_filters, state.cfg.pillars))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([
            r.position, r.ticker, fmt.csv_safe(r.name), r.sector or "", r.price, _times100(r.div_yield), r.streak, _times100(r.dgr_5),
            _times100(r.payout_fcf), _times100(r.payout_earnings), r.market_cap, r.dividend, r.safety, r.growth, r.valuation, r.score,
            _times100(r.margin_of_safety), " ".join(r.red_flags),
        ])
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="dgi-screener.csv"'})


def scatter(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        if con is None:
            return JSONResponse({"error": "no cache"}, status_code=503)
        points = scatter_points(con, parse_query(request.query_params, state.cfg.hard_filters, state.cfg.pillars))
    return JSONResponse({"points": [
        {"ticker": p.ticker, "name": p.name, "yield": _times100(p.div_yield), "dgr5": _times100(p.dgr_5), "score": p.score} for p in points
    ]})


def _ticker(request: Request) -> str:
    ticker = request.path_params["ticker"]
    if not TICKER.match(ticker):
        raise HTTPException(404)
    return ticker


def valuation_panel(view: Any, cfg: ScoringConfig) -> dict[str, Any] | None:
    dps = view.metrics.get("dividend_ttm")
    if not dps or dps <= 0:
        return None
    returns, growths = grid_axes(cfg.valuation)
    return {"returns": returns, "growths": growths, "grid": sensitivity_grid(dps, returns, growths), "dps": dps,
            "price": view.metrics.get("price")}


def company(request: Request) -> Response:
    state = request.app.state
    ticker = _ticker(request)
    with state.handle.connection() as con:
        if con is None:
            return no_cache(request)
        view = load_company(con, ticker)
        if view is None:
            raise HTTPException(404)
        annual = load_annual(con, view.ticker)
        meta = read_meta_from(con)
    charts = annual_charts(annual, view.metrics.get("div_yield_avg_5y"))
    context = {"c": view, "charts": [c.to_json() for c in charts], "valuation": valuation_panel(view, state.cfg), "footer": footer(meta)}
    return render(request, "company.html", context)


def daily(request: Request) -> Response:
    state = request.app.state
    ticker = _ticker(request)
    with state.handle.connection() as con:
        if con is None:
            return JSONResponse({"available": False, "notice": "There is no cache yet."}, status_code=503)
        view = load_company(con, ticker)
        if view is None:
            raise HTTPException(404)
        payments = load_payments(con, view.ticker)
        meta = read_meta_from(con)
    average = view.metrics.get("div_yield_avg_5y")
    if state.price_source is None:
        return JSONResponse({"available": False, "notice": "Daily prices are not configured; showing annual figures."})
    try:
        points = state.series_cache.get_or_load(meta.upstream_key if meta else "", view.ticker, lambda: state.price_source.daily(view.ticker))
    except DgiError as exc:
        return JSONResponse({"available": False, "notice": f"Daily prices are unavailable ({exc}); showing annual figures."})
    return JSONResponse({"available": True, "charts": [c.to_json() for c in daily_charts(points, payments, average)]})


def methodology(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        meta = None if con is None else read_meta_from(con)
    return render(request, "methodology.html", {"cfg": state.cfg, "footer": footer(meta)})


def health(request: Request) -> Response:
    with request.app.state.handle.connection() as con:
        if con is None:
            return JSONResponse({"status": "no_cache"})
        status = read_status_from(con)
    m = status.meta
    return JSONResponse({
        "status": "ok", "built_at": m.built_at, "upstream_key": m.upstream_key, "upstream_built_at": m.upstream_built_at,
        "contract_version": m.contract_version, "contract_warnings": m.contract_warnings, "metrics_version": m.metrics_version,
        "counts": status.counts, "scored": status.scored, "not_scored": status.not_scored,
    })


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    yield
    app.state.handle.close()


def create_app(settings: Settings, cfg: ScoringConfig, price_source: PriceSource | None = None) -> Starlette:
    app = Starlette(
        routes=[
            Route("/", screener), Route("/screener.csv", screener_csv), Route("/api/scatter", scatter),
            Route("/company/{ticker}", company), Route("/api/company/{ticker}/daily", daily),
            Route("/methodology", methodology), Route("/health", health),
            Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
        ],
        exception_handlers={404: not_found, BadQuery: bad_query},
        middleware=[Middleware(GZipMiddleware, minimum_size=1000)],  # a long company's daily series is close to 1 MB of JSON
        lifespan=lifespan,
    )
    app.state.cfg = cfg
    app.state.handle = CacheHandle(settings.cache_path)
    app.state.templates = make_templates()
    app.state.price_source = price_source
    app.state.series_cache = SeriesCache()
    return app
```

`src/dgi/web/templates/base.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}DGI Screener{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
  <script>try { var t = localStorage.getItem("dgi-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}</script>
</head>
<body>
  <header class="top">
    <a class="brand" href="/">DGI Screener</a>
    <nav aria-label="Main">
      <a href="/">Screener</a>
      <a href="/methodology">Methodology</a>
      <a href="/health">Health</a>
    </nav>
    <button id="theme-toggle" type="button" aria-label="Switch between light and dark">Theme</button>
  </header>
  <main>{% block content %}{% endblock %}</main>
  <footer>
    {% if footer %}Cache built {{ footer.built_at }} UTC · data from {{ footer.upstream_built_at }} · contract {{ footer.contract_version }}
      {% if footer.contract_warnings %}· <span class="warning">▲ {{ footer.contract_warnings }}</span>{% endif %}
    {% else %}No data yet.{% endif %}
    · Research tooling, not investment advice.
  </footer>
  <script src="/static/vendor/echarts.min.js"></script>
  <script src="/static/charts.js"></script>
  {% block scripts %}{% endblock %}
</body>
</html>
```

`src/dgi/web/templates/no_cache.html`:
```html
{% extends "base.html" %}
{% block title %}No data · DGI Screener{% endblock %}
{% block content %}
<section class="notice">
  <h1>There is no data yet</h1>
  <p>The cache has not been built. Run <code>dgi refresh</code> (or wait for the scheduled refresh job), then reload this page.</p>
</section>
{% endblock %}
```

`src/dgi/web/templates/error.html`:
```html
{% extends "base.html" %}
{% block title %}{{ title }} · DGI Screener{% endblock %}
{% block content %}
<section class="notice">
  <h1>{{ title }}</h1>
  <p>{{ message }}</p>
  <p><a href="/">Back to the screener</a></p>
</section>
{% endblock %}
```

`src/dgi/web/templates/methodology.html`:
```html
{% extends "base.html" %}
{% block title %}Methodology · DGI Screener{% endblock %}
{% block content %}
<h1>Methodology</h1>
<p>Companies are scored from 0 to 100 on four pillars. Each metric is mapped to 0–100 with the bands below (absolute bands, so a score does not shift when the universe changes), then averaged inside its pillar by weight; the total is the weighted mean of the pillars a company has data for. Missing metrics lower the company's coverage; below {{ (cfg.universe.min_coverage * 100)|round(0)|int }}% coverage it is not ranked. These values come from <code>config/scoring.yaml</code>.</p>

<h2>Pillar weights</h2>
<div class="table-wrap"><table>
  <thead><tr><th>Pillar</th><th class="num">Weight</th></tr></thead>
  <tbody>{% for p in pillars %}<tr><td>{{ pillar_labels[p] }}</td><td class="num">{{ (cfg.pillars[p] * 100)|round(0)|int }}%</td></tr>{% endfor %}</tbody>
</table></div>

<h2>Metric bands</h2>
<p class="note">Between two points the score is interpolated linearly; outside the first and last point it stays flat.</p>
<div class="table-wrap"><table>
  <thead><tr><th>Metric</th><th>Pillar</th><th class="num">Weight</th><th>Value → score</th></tr></thead>
  <tbody>
  {% for name, b in cfg.bands.items() %}
    <tr><td>{{ name|metric_label }}</td><td>{{ pillar_labels[b.pillar] }}</td><td class="num">{{ b.weight|num(1) }}</td>
      <td>{% for x, y in b.points %}{{ name|metric_value(x) }} → {{ y|round(0)|int }}{{ ', ' if not loop.last }}{% endfor %}</td></tr>
  {% endfor %}
  </tbody>
</table></div>

<h2>Screener filters (defaults)</h2>
<ul>
  <li>Minimum years of dividend increases: {{ cfg.hard_filters.min_streak }}</li>
  <li>Maximum payout of free cash flow: {{ cfg.hard_filters.max_payout_fcf|pct(0) }}</li>
  <li>Minimum market cap: {{ cfg.hard_filters.min_market_cap|big }}</li>
  <li>Filters are applied separately from the score and can be changed on the screener.</li>
</ul>

<h2>Who is scored</h2>
<ul>
  <li>At least {{ cfg.universe.min_dividend_years }} complete years of dividend history, a recent price and fundamentals from the last {{ cfg.universe.max_fundamentals_age_days }} days.</li>
  <li>Banks, insurers and REITs are listed as "not scored" (SIC ranges {% for lo, hi in cfg.universe.excluded_sic_ranges %}{{ lo }}–{{ hi }}{{ ', ' if not loop.last }}{% endfor %}): payout and coverage ratios do not describe those businesses.</li>
</ul>

<h2>Dividend handling</h2>
<ul>
  <li>Payments are restated to today's share basis using split events before they are summed, so splits never create fake cuts or raises.</li>
  <li>Annual dividends are calendar-year sums of complete years. A raise is more than {{ (cfg.metrics.raise_tolerance * 100)|round(1) }}% above the prior year, a cut more than {{ (cfg.metrics.cut_tolerance * 100)|round(1) }}% below it.</li>
  <li>An extra payment at least {{ cfg.metrics.special_ratio }}× the median regular one, in a year with more payments than usual, is a special dividend: excluded from the totals and flagged.</li>
  <li>Payout ratios use cash dividends paid over net income and over free cash flow, shown up to {{ (cfg.metrics.payout_cap * 100)|round(0)|int }}%. When a filer reports no dividends paid, the year's regular dividend per share times diluted shares stands in.</li>
  <li>The streak counts back to the start of the available dividend history (usually around 1970). A plus sign after it means the real record may be longer.</li>
</ul>

<h2>Fair value</h2>
<p>Gordon growth on the trailing dividend: value = dividend × (1 + g) / (r − g), with required return r = {{ (cfg.valuation.required_return * 100)|round(1) }}% and growth g equal to the 5-year dividend growth, kept between {{ (cfg.valuation.growth_floor * 100)|round(0)|int }}% and {{ (cfg.valuation.growth_cap * 100)|round(0)|int }}%. The range moves r and g by {{ (cfg.valuation.range_delta * 100)|round(1) }} points. It is a sensitivity aid, not a price target.</p>

<h2>Warnings</h2>
<p>Shown outside the score: a dividend cut or suspension in the last five years; a payout above 100% of earnings or of free cash flow; negative free cash flow; a dividend in the price data at or above the share price (likely a data error); and, as notes, special dividends and an irregular number of payments.</p>
{% endblock %}
```

`src/dgi/web/static/app.css`:
```css
/* Tokens follow the dataviz reference palette; dark values are their own selected steps, not an inversion. */
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11, 11, 11, 0.10); --accent: #2a78d6; --accent-ink: #184f95;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
  --critical: #d03b3b; --critical-ink: #a42a2a;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255, 255, 255, 0.10); --accent: #3987e5; --accent-ink: #86b6ef;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
    --critical: #ec6b6b; --critical-ink: #ff9a9a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255, 255, 255, 0.10); --accent: #3987e5; --accent-ink: #86b6ef;
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  --critical: #ec6b6b; --critical-ink: #ff9a9a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
a { color: var(--accent-ink); }
h1 { font-size: 1.6rem; margin: 1.2rem 0 0.6rem; }
h2 { font-size: 1.15rem; margin: 1.6rem 0 0.5rem; }
main { max-width: 1200px; margin: 0 auto; padding: 0 16px 32px; }
code { background: var(--surface); border: 1px solid var(--ring); border-radius: 4px; padding: 0 4px; }
.top { display: flex; align-items: center; gap: 24px; padding: 10px 16px; background: var(--surface); border-bottom: 1px solid var(--ring); }
.top nav { display: flex; gap: 16px; flex: 1; }
.top a { text-decoration: none; color: var(--ink-2); }
.top .brand { color: var(--ink); font-weight: 600; }
button, .actions a.export { font: inherit; cursor: pointer; border: 1px solid var(--ring); background: var(--surface); color: var(--ink); border-radius: 6px; padding: 4px 12px; text-decoration: none; }
button[type="submit"] { background: var(--accent); border-color: var(--accent); color: #fff; }
footer { max-width: 1200px; margin: 0 auto; padding: 16px; color: var(--muted); font-size: 13px; }
.warning, .flag.red { color: var(--critical-ink); }
.note, .sub { color: var(--ink-2); font-size: 13px; margin: 4px 0 8px; }
.notice { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 8px 16px; margin: 16px 0; }
.strip, .tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
.stat, .tile { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 10px 16px; min-width: 150px; display: flex; flex-direction: column; }
.value { font-size: 1.5rem; font-weight: 600; }
.label { color: var(--ink-2); font-size: 13px; }
.filters { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 12px 16px; }
.field-row { display: flex; flex-wrap: wrap; gap: 12px 16px; }
.filters label { display: flex; flex-direction: column; font-size: 13px; color: var(--ink-2); gap: 2px; }
.filters label.check { flex-direction: row; align-items: center; gap: 6px; }
input, select { font: inherit; padding: 4px 6px; border: 1px solid var(--axis); border-radius: 6px; background: var(--page); color: var(--ink); max-width: 150px; }
.weights { border: 0; padding: 8px 0 0; margin: 8px 0 0; display: flex; flex-wrap: wrap; gap: 8px 24px; border-top: 1px solid var(--grid); }
.weights legend { font-size: 13px; color: var(--ink-2); padding: 0; }
.actions { display: flex; gap: 12px; align-items: center; margin-top: 10px; }
.table-wrap { overflow-x: auto; margin: 8px 0; border: 1px solid var(--ring); border-radius: 8px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
caption { text-align: left; padding: 8px 12px; color: var(--ink-2); font-size: 13px; }
th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--grid); white-space: nowrap; }
td.name { white-space: normal; min-width: 140px; max-width: 220px; }
th { color: var(--ink-2); font-weight: 600; font-size: 13px; }
th a { color: inherit; text-decoration: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; }
tbody tr:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
.flag-count { color: var(--critical-ink); font-size: 13px; }
.pager { display: flex; gap: 16px; align-items: center; margin: 8px 0; }
.company-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.ticker { color: var(--ink-2); font-weight: 400; font-size: 1.1rem; }
.badge { background: var(--accent); color: #fff; border-radius: 12px; padding: 8px 20px; display: flex; flex-direction: column; align-items: center; margin-top: 12px; }
.badge .label { color: #fff; }
.badge.muted { background: var(--surface); border: 1px solid var(--ring); } .badge.muted .label { color: var(--ink-2); }
.flags { list-style: none; padding: 0; margin: 8px 0; display: grid; gap: 4px; }
.flag { padding: 4px 0; }
.bars { display: grid; gap: 6px; max-width: 640px; }
.bar-row { display: grid; grid-template-columns: 160px 1fr 40px; align-items: center; gap: 10px; }
.track { height: 10px; background: var(--grid); border-radius: 5px; overflow: hidden; }
.fill { height: 100%; background: var(--accent); border-radius: 5px; }
.chart-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 16px; margin: 12px 0; }
.chart-card { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 10px 12px; }
.chart-card h3 { font-size: 0.95rem; margin: 0 0 4px; }
.chart { width: 100%; height: 260px; } .chart.tall { height: 420px; background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; }
details summary { cursor: pointer; color: var(--ink-2); font-size: 13px; margin-top: 4px; }
@media (max-width: 640px) { .chart-grid { grid-template-columns: 1fr; } .bar-row { grid-template-columns: 110px 1fr 36px; } .top { gap: 12px; flex-wrap: wrap; } }
```

- [ ] **Step A4: Write `charts.js`**

It builds ECharts options from the page's JSON: tokens read from the CSS variables, 2px lines with no point markers, rounded bar tops, hairline grid, dashed labelled reference lines, a legend whenever a chart has two or more series, an axis tooltip that lists every series (value first), a "Table" disclosure built with `textContent`, percent ticks with enough decimals that they never repeat, and a zero baseline for bars, ratios and percentages that cannot go negative. Charts redraw when the theme changes.

`src/dgi/web/static/charts.js`:
```javascript
/* Charts: ECharts options built from the page's JSON. Colours come from the CSS tokens so light and dark both work.
   Names and values from the data are written with textContent or escaped; nothing is concatenated into markup raw. */
(function () {
  "use strict";

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function tokens() {
    return { ink: css("--ink"), ink2: css("--ink-2"), muted: css("--muted"), grid: css("--grid"), axis: css("--axis"),
             surface: css("--surface"), series: [css("--series-1"), css("--series-2"), css("--series-3")] };
  }
  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }
  function format(unit, v) {
    if (v === null || v === undefined || Number.isNaN(v)) return "–";
    if (unit === "pct") return (v * 100).toFixed(1) + "%";
    if (unit === "usd") return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (unit === "x") return v.toFixed(1) + "x";
    if (unit === "millions") return v.toLocaleString(undefined, { maximumFractionDigits: 1 }) + "M";
    return String(v);
  }
  function axisFormat(unit, v, digits) {
    if (unit === "pct") return (v * 100).toFixed(digits) + "%";
    if (unit === "usd") return "$" + v;
    if (unit === "x") return v + "x";
    return v;
  }
  function valueRange(spec) {
    var all = [];
    spec.series.forEach(function (s) { s.values.forEach(function (v) { if (v !== null && v !== undefined) all.push(v); }); });
    return all.length ? { min: Math.min.apply(null, all), max: Math.max.apply(null, all) } : { min: 0, max: 1 };
  }

  function buildOption(spec, t) {
    var multi = spec.series.length > 1;
    var time = spec.xType === "time";
    var range = valueRange(spec);
    var spread = (range.max - range.min) * 100;
    var digits = spec.unit === "pct" ? (spread < 1 ? 2 : spread < 10 ? 1 : 0) : 0;  // narrow ranges need decimals, or ticks repeat
    // bars, ratios and percentages start at zero when they cannot go negative; prices and per-share lines fit their range
    var zero = spec.kind === "bar" || ((spec.unit === "x" || spec.unit === "pct") && range.min >= 0);
    var series = spec.series.map(function (s) {
      var color = t.series[s.slot - 1];
      var data = time ? s.values.map(function (v, i) { return [spec.x[i], v]; }) : s.values;
      var base = { name: s.name, type: spec.kind, data: data, itemStyle: { color: color }, emphasis: { focus: "series" } };
      if (spec.kind === "line") {
        base.symbol = "none"; base.lineStyle = { width: 2, color: color }; base.connectNulls = false;
      } else {
        base.barMaxWidth = 28; base.itemStyle = { color: color, borderRadius: [4, 4, 0, 0] };
      }
      return base;
    });
    if (spec.references && spec.references.length && series.length) {
      series[0].markLine = {
        silent: true, symbol: "none",
        lineStyle: { color: t.muted, type: "dashed", width: 1 },
        label: { color: t.ink2, formatter: function (p) { return p.name; }, position: "insideEndTop" },
        data: spec.references.map(function (r) { return { name: r.name, yAxis: r.value }; })
      };
    }
    return {
      animation: false,
      textStyle: { color: t.ink2, fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
      grid: { left: 48, right: 16, top: multi ? 36 : 12, bottom: 28, containLabel: false },
      legend: multi ? { show: true, top: 0, left: 0, textStyle: { color: t.ink2 }, icon: "roundRect", itemWidth: 14, itemHeight: 3 } : { show: false },
      xAxis: time
        ? { type: "time", axisLine: { lineStyle: { color: t.axis } }, axisLabel: { color: t.muted, hideOverlap: true }, splitLine: { show: false } }
        : { type: "category", data: spec.x, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, axisLabel: { color: t.muted, hideOverlap: true } },
      yAxis: { type: "value", min: zero ? 0 : null, scale: !zero, axisLabel: { color: t.muted, formatter: function (v) { return axisFormat(spec.unit, v, digits); } },
               splitLine: { lineStyle: { color: t.grid, width: 1 } } },
      tooltip: {
        trigger: "axis", confine: true, axisPointer: { type: "line", lineStyle: { color: t.axis } },
        backgroundColor: t.surface, borderColor: t.grid, textStyle: { color: t.ink },
        formatter: function (params) {
          var rows = params.map(function (p) {
            var v = Array.isArray(p.value) ? p.value[1] : p.value;
            return '<div><span style="display:inline-block;width:12px;height:3px;background:' + esc(p.color) + ';margin-right:6px;vertical-align:middle"></span>' +
                   "<strong>" + esc(format(spec.unit, v)) + '</strong> <span style="color:' + esc(t.ink2) + '">' + esc(p.seriesName) + "</span></div>";
          }).join("");
          var head = Array.isArray(params[0].value) ? String(params[0].value[0]).slice(0, 10) : params[0].axisValueLabel;
          return '<div style="color:' + esc(t.ink2) + '">' + esc(head) + "</div>" + rows;
        }
      },
      series: series
    };
  }

  function tableFor(spec) {
    var table = document.createElement("table");
    var head = table.createTHead().insertRow();
    ["", ].concat(spec.series.map(function (s) { return s.name; })).forEach(function (name, i) {
      var th = document.createElement("th"); th.textContent = i === 0 ? (spec.xType === "time" ? "Date" : "Year") : name; head.appendChild(th);
    });
    var body = table.createTBody();
    var step = spec.x.length > 400 ? Math.ceil(spec.x.length / 400) : 1;
    spec.x.forEach(function (x, i) {
      if (i % step) return;
      var tr = body.insertRow();
      var first = tr.insertCell(); first.textContent = String(x);
      spec.series.forEach(function (s) { var td = tr.insertCell(); td.className = "num"; td.textContent = format(spec.unit, s.values[i]); });
    });
    return table;
  }

  var live = [];

  function dropCharts(card) {
    live = live.filter(function (c) { if (card.contains(c.chart.getDom())) { c.chart.dispose(); return false; } return true; });
  }

  function addChart(host, spec) {
    var card = document.createElement("div"); card.className = "chart-card"; card.dataset.chartId = spec.id;
    var h = document.createElement("h3"); h.textContent = spec.title; card.appendChild(h);
    var el = document.createElement("div"); el.className = "chart"; el.setAttribute("role", "img");
    el.setAttribute("aria-label", spec.title + ". The values are in the table below the chart.");
    card.appendChild(el);
    if (spec.note) { var n = document.createElement("div"); n.className = "note"; n.textContent = spec.note; card.appendChild(n); }
    var d = document.createElement("details"); var s = document.createElement("summary"); s.textContent = "Table";
    d.appendChild(s); var wrap = document.createElement("div"); wrap.className = "table-wrap"; wrap.appendChild(tableFor(spec)); d.appendChild(wrap);
    card.appendChild(d);
    host.appendChild(card);
    var chart = echarts.init(el);
    chart.setOption(buildOption(spec, tokens()));
    live.push({ chart: chart, render: function () { chart.setOption(buildOption(spec, tokens()), true); } });
    return card;
  }

  window.dgiInitCompany = function (ticker) {
    var host = document.getElementById("charts");
    var annual = JSON.parse(document.getElementById("charts-data").textContent);
    annual.forEach(function (spec) { addChart(host, spec); });
    var notice = document.getElementById("daily-notice");
    fetch("/api/company/" + encodeURIComponent(ticker) + "/daily").then(function (r) { return r.json(); }).then(function (body) {
      if (!body.available) { notice.textContent = body.notice || ""; return; }
      var yieldCard = host.querySelector('[data-chart-id="yield"]');
      body.charts.forEach(function (spec) {
        var card = addChart(host, spec);            // created in the page first, so ECharts can measure it
        if (spec.id === "yield_daily" && yieldCard) { dropCharts(yieldCard); yieldCard.replaceWith(card); }
        else if (spec.id === "price") { host.insertBefore(card, host.firstChild); }
      });
      window.dispatchEvent(new Event("resize"));
    }).catch(function () { notice.textContent = "Daily prices could not be loaded; showing annual figures."; });
  };

  window.dgiInitScatter = function () {
    var el = document.getElementById("scatter");
    if (!el) return;
    fetch(el.dataset.scatterUrl).then(function (r) { return r.json(); }).then(function (body) {
      var points = body.points || [];
      var chart = echarts.init(el);
      function render() {
        var t = tokens();
        var scores = points.map(function (p) { return p.score || 0; });
        var lo = Math.min.apply(null, scores.concat([0])), hi = Math.max.apply(null, scores.concat([1]));
        chart.setOption({
          animation: false, textStyle: { color: t.ink2 },
          grid: { left: 56, right: 24, top: 16, bottom: 44 },
          xAxis: { name: "Dividend yield (%)", nameLocation: "middle", nameGap: 28, type: "value", scale: true, axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.axis } }, splitLine: { lineStyle: { color: t.grid } } },
          yAxis: { name: "5-year dividend growth (%)", nameLocation: "middle", nameGap: 40, type: "value", scale: true, axisLabel: { color: t.muted }, splitLine: { lineStyle: { color: t.grid } } },
          tooltip: { trigger: "item", confine: true, backgroundColor: t.surface, borderColor: t.grid, textStyle: { color: t.ink },
            formatter: function (p) { var d = p.data; return "<strong>" + esc(d.ticker) + "</strong> " + esc(d.name) + "<br>Yield " + d.value[0].toFixed(1) + "% · 5y growth " + d.value[1].toFixed(1) + "%<br>Score " + (d.score === null ? "–" : d.score.toFixed(0)); } },
          series: [{ type: "scatter", itemStyle: { color: t.series[0], opacity: 0.8, borderColor: t.surface, borderWidth: 2 },
            symbolSize: function (v, p) { var s = p.data.score || 0; return 8 + 22 * (s - lo) / (hi - lo || 1); },
            data: points.map(function (p) { return { value: [p.yield, p.dgr5], ticker: p.ticker, name: p.name, score: p.score }; }) }]
        }, true);
      }
      render();
      live.push({ chart: chart, render: render });
      chart.on("click", function (p) { if (p.data && p.data.ticker) window.location.href = "/company/" + encodeURIComponent(p.data.ticker); });
    });
  };

  function rerenderAll() { live.forEach(function (c) { c.render(); }); }
  window.addEventListener("resize", function () { live.forEach(function (c) { c.chart.resize(); }); });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerenderAll);
  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.getElementById("theme-toggle");
    if (toggle) toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var dark = root.dataset.theme ? root.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("dgi-theme", root.dataset.theme); } catch (e) {}
      rerenderAll();
    });
    document.querySelectorAll(".filters input[type=range]").forEach(function (r) {
      r.addEventListener("input", function () { var o = r.parentElement.querySelector("output"); if (o) o.textContent = r.value; });
    });
  });
})();
```

```bash
node --check src/dgi/web/static/charts.js
```
Expected: no output (syntax is valid).

- [ ] **Step A5: Run to verify it passes, then commit**

Run: `uv run pytest tests/test_web_app.py tests/test_architecture.py`
Expected: PASS (7 + 7 tests, none skipped).

```bash
git add src/dgi/web tests/test_web_app.py
git commit -m "feat: web app skeleton with health, methodology, static assets and no-cache page"
```

**Checkpoint B: screener**

- [ ] **Step B1: Write the failing tests**

`tests/test_web_screener.py` (UNIT: screener page, filters, errors, sorting, unscored view, CSV, escaping, scatter API):
```python
import csv
import io
import re

import pytest
from starlette.testclient import TestClient

from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG, make_meta, persist, scored_cache
from tests.web_fixtures import COMPANIES, sample_cache


@pytest.fixture
def web(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        yield client


def rows(html: str) -> list[str]:
    """Tickers linked in the ranked table, in order."""
    import re
    return re.findall(r'<td><a href="/company/([A-Z.\-]+)">', html)


def test_the_default_screen_lists_the_companies_that_pass_the_config_filters(web):
    response = web.get("/")
    assert response.status_code == 200
    assert rows(response.text) == ["AAA", "BBB"]
    assert "7</span><span class=\"label\">companies listed" in response.text
    assert "Ranked companies (2)" in response.text and "Export CSV" in response.text


def test_a_streak_that_reaches_the_start_of_the_history_gets_a_plus(web):
    text = web.get("/").text
    assert re.search(r'<td class="num">12\+</td>', text) and re.search(r'<td class="num">30</td>', text)   # BBB: a 12-year streak on 12 years of history; AAA: 30 of 45
    assert "A plus after a streak" in text
    assert re.search(r'<span class="value">12\+</span>', web.get("/company/BBB").text)


def test_the_filter_bar_is_filled_from_the_url_and_the_config_defaults(web):
    text = web.get("/?min_streak=9&sector=Health+Care&w_growth=0").text
    assert 'name="min_streak" min="0" step="1" value="9"' in text
    assert '<option value="Health Care" selected>' in text
    assert 'name="w_growth" min="0" max="100" step="5" value="0"' in text
    assert rows(text) == ["BBB"]


def test_loosening_a_filter_adds_a_company_and_a_warning_badge_shows(web):
    text = web.get("/?max_payout_fcf=").text
    assert rows(text) == ["AAA", "BBB", "CCC"]
    assert "▲ 1 warning" in text


@pytest.mark.parametrize("query, param", [("min_streak=abc", "min_streak"), ("page=0", "page"), ("sort=ticker;drop+table+scores", "sort"), ("dir=up", "dir"), ("w_dividend=0&w_safety=0&w_growth=0&w_valuation=0", "w_dividend")])
def test_bad_parameters_answer_422_naming_the_parameter(web, query, param):
    response = web.get("/?" + query)
    assert response.status_code == 422 and param in response.text
    assert web.get("/screener.csv?" + query).status_code == 422
    assert web.get("/api/scatter?" + query).status_code == 422


def test_sort_links_flip_direction_and_keep_the_filters(web):
    text = web.get("/?sort=yield&dir=desc&min_streak=3").text
    assert 'aria-sort="descending"' in text
    assert "sort=yield&amp;dir=asc" in text and "min_streak=3" in text


def test_paging_links_appear_only_when_there_is_more_than_one_page(web):
    assert "Next" not in web.get("/").text
    text = web.get("/?min_streak=0&max_payout_fcf=&min_cap_bn=0&size=10").text
    assert "Next" not in text      # five companies fit on one page of ten
    assert "Page " not in text


def test_not_scored_companies_are_listed_with_their_reason(web):
    text = web.get("/?unscored=1").text
    assert "Not scored (2)" in text and "BANK" in text and "NEWC" in text
    assert "Bank, insurer or REIT" in text and "Fewer complete years" in text
    assert "Export CSV" not in text


def test_csv_export_matches_the_table_and_neutralizes_formulas(tmp_path):
    con = scored_cache(COMPANIES)
    con.execute("UPDATE company_dim SET name = '=HYPERLINK(\"http://evil\")' WHERE ticker = 'AAA'")
    persist(con, tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        response = client.get("/screener.csv?min_streak=0&max_payout_fcf=&min_cap_bn=0")
    assert response.headers["content-type"].startswith("text/csv") and "attachment" in response.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["ticker"] for r in table] == ["AAA", "EEE", "BBB", "DDD", "CCC"]   # best score first
    aaa = next(r for r in table if r["ticker"] == "AAA")
    assert aaa["name"].startswith("'=") and aaa["yield_pct"] == "2.5" and aaa["streak"] == "30"
    ccc = next(r for r in table if r["ticker"] == "CCC")
    assert ccc["red_flags"] == "payout_fcf_over_100"


def test_company_names_are_escaped_in_the_page(tmp_path):
    con = scored_cache(COMPANIES)
    con.execute("UPDATE company_dim SET name = '<script>alert(1)</script>' WHERE ticker = 'AAA'")
    persist(con, tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        text = client.get("/").text
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_scatter_api_returns_percent_points_for_the_filtered_set(web):
    body = web.get("/api/scatter").json()
    points = {p["ticker"]: p for p in body["points"]}
    assert set(points) == {"AAA", "BBB"}
    assert points["AAA"]["yield"] == 2.5 and points["AAA"]["dgr5"] == 7.0 and points["AAA"]["score"] > 0
    assert set(web.get("/api/scatter?min_streak=0&max_payout_fcf=&min_cap_bn=0").json()["points"][0]) == {"ticker", "name", "yield", "dgr5", "score"}
```

- [ ] **Step B2: Run to verify it fails**

Run: `uv run pytest tests/test_web_screener.py`
Expected: FAIL (the templates for `/` do not exist yet: `jinja2.exceptions.TemplateNotFound: screener.html`).

- [ ] **Step B3: Write `screener.html`**

`src/dgi/web/templates/screener.html`:
```html
{% extends "base.html" %}
{% block title %}Screener · DGI Screener{% endblock %}
{% macro th(label, key, numeric=False) -%}
  {%- set flip = ('asc' if q.descending else 'desc') if q.sort == key else ('asc' if key in ('ticker', 'name', 'sector') else 'desc') -%}
  <th{% if numeric %} class="num"{% endif %} aria-sort="{{ ('descending' if q.descending else 'ascending') if q.sort == key else 'none' }}">
    <a href="/?{{ qs(q.params, sort=key, dir=flip, page=1) }}">{{ label }}{% if q.sort == key %} {{ '▼' if q.descending else '▲' }}{% endif %}</a>
  </th>
{%- endmacro %}
{% block content %}
<h1>Dividend growth screener</h1>
<section class="strip" aria-label="Universe">
  <div class="stat"><span class="value">{{ stats.universe }}</span><span class="label">companies listed</span></div>
  <div class="stat"><span class="value">{{ stats.scored }}</span><span class="label">scored</span></div>
  <div class="stat"><span class="value">{{ stats.passing }}</span><span class="label">pass the filters</span></div>
  <div class="stat"><span class="value">{{ stats.median_yield|pct }}</span><span class="label">median yield of those</span></div>
</section>

<form class="filters" method="get" action="/">
  <div class="field-row">
    <label>Min streak (years)<input type="number" name="min_streak" min="0" step="1" value="{{ q.params.min_streak }}"></label>
    <label>Yield from (%)<input type="number" name="min_yield" min="0" step="0.1" value="{{ q.params.min_yield }}"></label>
    <label>Yield to (%)<input type="number" name="max_yield" min="0" step="0.1" value="{{ q.params.max_yield }}" placeholder="no limit"></label>
    <label>Max payout of FCF (%)<input type="number" name="max_payout_fcf" min="0" step="5" value="{{ q.params.max_payout_fcf }}" placeholder="no limit"></label>
    <label>Max payout of earnings (%)<input type="number" name="max_payout_eps" min="0" step="5" value="{{ q.params.max_payout_eps }}" placeholder="no limit"></label>
    <label>Min market cap ($B)<input type="number" name="min_cap_bn" min="0" step="0.5" value="{{ q.params.min_cap_bn }}"></label>
    <label>Min score<input type="number" name="min_score" min="0" max="100" step="5" value="{{ q.params.min_score }}"></label>
    <label>Sector
      <select name="sector">
        <option value="">All</option>
        {% for s in sectors %}<option value="{{ s }}"{% if q.sector == s %} selected{% endif %}>{{ s }}</option>{% endfor %}
      </select>
    </label>
    <label class="check"><input type="checkbox" name="unscored" value="1"{% if q.show_unscored %} checked{% endif %}> Show companies that are not scored</label>
  </div>
  <fieldset class="weights">
    <legend>Pillar weights (relative; applied to the stored pillar scores)</legend>
    {% for p in pillars %}
      <label>{{ pillar_labels[p] }}
        <input type="range" name="w_{{ p }}" min="0" max="100" step="5" value="{{ q.params['w_' ~ p] }}">
        <output>{{ q.params['w_' ~ p] }}</output>
      </label>
    {% endfor %}
  </fieldset>
  <input type="hidden" name="sort" value="{{ q.sort }}"><input type="hidden" name="dir" value="{{ 'desc' if q.descending else 'asc' }}">
  <div class="actions"><button type="submit">Apply</button> <a href="/">Reset</a>
    {% if not q.show_unscored %}<a class="export" href="/screener.csv?{{ qs(q.params) }}">Export CSV</a>{% endif %}</div>
</form>

{% if q.show_unscored %}
  <h2>Not scored ({{ unscored_total }})</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Ticker</th><th>Company</th><th>Sector</th><th>Why</th></tr></thead>
    <tbody>
    {% for r in unscored %}
      <tr><td><a href="/company/{{ r.ticker }}">{{ r.ticker }}</a></td><td>{{ r.name }}</td><td>{{ r.sector or dash }}</td><td>{{ r.reason|reason_label }}</td></tr>
    {% else %}<tr><td colspan="4">Nothing here.</td></tr>{% endfor %}
    </tbody>
  </table></div>
{% else %}
  <section aria-labelledby="scatter-h">
    <h2 id="scatter-h">Yield against 5-year dividend growth</h2>
    <p class="note">Each dot is a company that passes the filters; bigger dots score higher. Click a dot to open the company. The table below lists the same companies.</p>
    <div id="scatter" class="chart tall" data-scatter-url="/api/scatter?{{ qs(q.params) }}" role="img"
         aria-label="Scatter of dividend yield against five-year dividend growth for the companies in the table"></div>
  </section>
  <h2>Ranked companies ({{ page.total }})</h2>
  <div class="table-wrap"><table>
    <thead><tr>
      <th class="num">#</th>{{ th('Ticker', 'ticker') }}{{ th('Company', 'name') }}{{ th('Sector', 'sector') }}
      {{ th('Price', 'price', True) }}{{ th('Yield', 'yield', True) }}{{ th('Streak', 'streak', True) }}{{ th('5y DGR', 'dgr5', True) }}
      {{ th('Payout FCF', 'payout_fcf', True) }}{{ th('Dividend', 'dividend', True) }}{{ th('Safety', 'safety', True) }}
      {{ th('Growth', 'growth', True) }}{{ th('Value', 'valuation', True) }}{{ th('Score', 'score', True) }}<th>Flags</th>
    </tr></thead>
    <tbody>
    {% for r in page.rows %}
      <tr>
        <td class="num">{{ r.position }}</td>
        <td><a href="/company/{{ r.ticker }}">{{ r.ticker }}</a></td><td class="name">{{ r.name }}</td><td>{{ r.sector or dash }}</td>
        <td class="num">{{ r.price|money }}</td><td class="num">{{ r.div_yield|pct }}</td><td class="num">{{ r.streak|streak_label(r.years_history) }}</td>
        <td class="num">{{ r.dgr_5|pct }}</td><td class="num">{{ r.payout_fcf|pct(0) }}</td>
        <td class="num">{{ r.dividend|score }}</td><td class="num">{{ r.safety|score }}</td><td class="num">{{ r.growth|score }}</td><td class="num">{{ r.valuation|score }}</td>
        <td class="num strong">{{ r.score|score }}</td>
        <td>{% if r.red_flags %}<span class="flag-count" title="{{ r.red_flags|join(', ') }}">▲ {{ r.red_flags|length }} warning{{ 's' if r.red_flags|length != 1 }}</span>{% endif %}</td>
      </tr>
    {% else %}<tr><td colspan="15">No company passes these filters. Loosen one of them.</td></tr>{% endfor %}
    </tbody>
  </table></div>
  <p class="note">A plus after a streak means the record reaches back to the start of the available dividend history, so the real streak may be longer.</p>
  {% if page.pages > 1 %}
    <nav class="pager" aria-label="Pages">
      {% if page.page > 1 %}<a href="/?{{ qs(q.params, page=page.page - 1) }}">Previous</a>{% endif %}
      <span>Page {{ page.page }} of {{ page.pages }}</span>
      {% if page.page < page.pages %}<a href="/?{{ qs(q.params, page=page.page + 1) }}">Next</a>{% endif %}
    </nav>
  {% endif %}
{% endif %}
{% endblock %}
{% block scripts %}<script>window.dgiInitScatter && window.dgiInitScatter();</script>{% endblock %}
```

- [ ] **Step B4: Run to verify it passes, then commit**

Run: `uv run pytest tests/test_web_screener.py`
Expected: PASS (15 passed).

```bash
git add src/dgi/web/templates/screener.html tests/test_web_screener.py
git commit -m "feat: screener page with filters, weights, scatter, paging and CSV export"
```

**Checkpoint C: company page**

- [ ] **Step C1: Write the failing tests**

`tests/test_web_company.py` (UNIT: company page, flags, not-scored page, daily drill-down and its cache):
```python
import datetime as dt
import json
import re

import pytest
from starlette.testclient import TestClient

from dgi.cache.build import new_path, swap_in
from dgi.errors import ApiUnavailable
from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG, cache_with, make_meta, persist
from tests.web_fixtures import D, add_history, sample_cache

DAILY = [(D(2025, 1, 1) + dt.timedelta(days=i), 50.0 + i * 0.01) for i in range(400)]


class StubSource:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def daily(self, ticker):
        self.calls.append(ticker)
        if self.error:
            raise self.error
        return DAILY


def app_for(tmp_path, source=None):
    return TestClient(create_app(Settings(data_dir=tmp_path), CFG, source))


@pytest.fixture
def web(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with app_for(tmp_path) as client:
        yield client


def embedded_charts(html: str) -> list[dict]:
    return json.loads(re.search(r'<script type="application/json" id="charts-data">(.*?)</script>', html, re.S).group(1))


def test_a_scored_company_shows_its_header_pillars_tiles_valuation_and_explanation(web):
    response = web.get("/company/AAA")
    text = response.text
    assert response.status_code == 200
    assert "AAA Inc" in text and "Consumer Staples" in text and "$60.00" in text
    assert 'aria-label="Score ' in text and "Score by pillar" in text and "Dividend record" in text and "Data coverage 100%" in text
    assert "Fair value (range)" in text and "Margin of safety" in text
    assert "Fair value per share by required return" in text and "9.0%" in text
    assert "Why this score" in text and "Years of dividend increases" in text and "Payout of free cash flow" in text
    assert "Warning:" not in text


def test_the_page_embeds_the_annual_charts_as_data(web):
    ids = [c["id"] for c in embedded_charts(web.get("/company/AAA").text)]
    assert ids[:3] == ["dps", "yield", "dps_growth"] and {"payout", "per_share", "leverage", "coverage", "shares"} <= set(ids)
    dps = embedded_charts(web.get("/company/AAA").text)[0]
    assert dps["x"][0] == 2014 and dps["series"][0]["values"][0] == 1.0


def test_a_company_with_a_red_flag_shows_it_in_words(web):
    text = web.get("/company/CCC").text
    assert "Warning:" in text and "Dividends paid exceed free cash flow (payout 110%)" in text


def test_a_company_that_is_not_scored_shows_why_and_no_score(web):
    text = web.get("/company/BANK").text
    assert "Not scored" in text and "Bank, insurer or REIT" in text
    assert "Why this score" not in text and "Score by pillar" not in text and "DGI score" not in text
    assert embedded_charts(text) == []


def test_lookup_ignores_case(web):
    assert web.get("/company/aaa").status_code == 200


def test_the_ticker_reaches_the_script_as_json_not_markup(web):
    assert 'window.dgiInitCompany("AAA")' in web.get("/company/AAA").text


# daily drill-down -----------------------------------------------------------------------------------------------------

def test_the_daily_endpoint_returns_price_and_yield_charts(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    source = StubSource()
    with app_for(tmp_path, source) as client:
        body = client.get("/api/company/AAA/daily").json()
    assert body["available"] is True
    assert [c["id"] for c in body["charts"]] == ["price", "yield_daily"]
    assert body["charts"][0]["xType"] == "time" and len(body["charts"][0]["x"]) == 400
    assert source.calls == ["AAA"]


def test_daily_series_are_cached_per_upstream_key_and_reloaded_when_it_changes(tmp_path):
    live = sample_cache(tmp_path / "dgi.duckdb")
    source = StubSource()
    with app_for(tmp_path, source) as client:
        client.get("/api/company/AAA/daily")
        client.get("/api/company/AAA/daily")
        assert source.calls == ["AAA"]
        con = cache_with({"AAA": ("2080", {})})
        add_history(con, "AAA")
        swap_in(persist(con, new_path(live), make_meta(upstream_key="hash-2|2026-10-06T06:00:00+00:00")), live)
        client.get("/api/company/AAA/daily")
    assert source.calls == ["AAA", "AAA"]


def test_large_responses_are_compressed(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with app_for(tmp_path, StubSource()) as client:
        response = client.get("/api/company/AAA/daily", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip" and response.json()["available"] is True


def test_when_the_api_is_down_the_endpoint_says_so_and_the_page_keeps_working(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with app_for(tmp_path, StubSource(ApiUnavailable("cannot reach the investment API"))) as client:
        body = client.get("/api/company/AAA/daily").json()
        assert body["available"] is False and "unavailable" in body["notice"] and "annual figures" in body["notice"]
        assert client.get("/company/AAA").status_code == 200


def test_without_a_price_source_the_endpoint_falls_back(web):
    body = web.get("/api/company/AAA/daily").json()
    assert body["available"] is False and "annual" in body["notice"]
```

- [ ] **Step C2: Run to verify it fails**

Run: `uv run pytest tests/test_web_company.py`
Expected: FAIL (`TemplateNotFound: company.html`).

- [ ] **Step C3: Write `company.html`**

`src/dgi/web/templates/company.html`:
```html
{% extends "base.html" %}
{% block title %}{{ c.ticker }} · DGI Screener{% endblock %}
{% block content %}
{% set m = c.metrics %}
{% set s = c.score %}
<header class="company-head">
  <div>
    <h1>{{ c.name }} <span class="ticker">{{ c.ticker }}</span></h1>
    <p class="sub">{{ c.sector_group or dash }}{% if c.sic_description %} · {{ c.sic_description }}{% endif %} · {{ m.price|money }}{% if m.price_date %} on {{ m.price_date }}{% endif %}</p>
  </div>
  {% if c.status == 'scored' %}
    <div class="badge" aria-label="Score {{ s.total|score }} out of 100"><span class="value">{{ s.total|score }}</span><span class="label">DGI score</span></div>
  {% else %}
    <div class="badge muted"><span class="label">Not scored</span></div>
  {% endif %}
</header>

{% if c.status != 'scored' %}
  <section class="notice"><p><strong>Not scored.</strong> {{ c.reason|reason_label }}.</p></section>
{% endif %}

{% if c.flags %}
  <ul class="flags" aria-label="Flags">
    {% for f in c.flags %}
      <li class="flag {{ f.severity }}"><span aria-hidden="true">{{ '▲' if f.severity == 'red' else 'ℹ' }}</span>
        <strong>{{ 'Warning' if f.severity == 'red' else 'Note' }}:</strong> {{ f.text }}</li>
    {% endfor %}
  </ul>
{% endif %}

{% if c.status == 'scored' %}
<section aria-labelledby="pillars-h">
  <h2 id="pillars-h">Score by pillar</h2>
  <div class="bars">
    {% for p in pillars %}
      <div class="bar-row"><span class="name">{{ pillar_labels[p] }}</span>
        <div class="track"><div class="fill" style="width: {{ (s[p] or 0)|round(0)|int }}%"></div></div>
        <span class="num">{{ s[p]|score }}</span></div>
    {% endfor %}
  </div>
  <p class="note">Data coverage {{ (s.coverage * 100)|round(0)|int }}% of the weighted metrics.</p>
</section>
{% endif %}

<section class="tiles" aria-label="Key figures">
  <div class="tile"><span class="label">Dividend yield</span><span class="value">{{ m.div_yield|pct }}</span></div>
  <div class="tile"><span class="label">5-year dividend growth</span><span class="value">{{ m.dgr_5|pct }}</span></div>
  <div class="tile"><span class="label">Years of increases</span><span class="value">{{ m.streak|streak_label(m.years_history) }}</span></div>
  <div class="tile"><span class="label">Payout of earnings</span><span class="value">{{ m.payout_earnings|pct(0) }}</span></div>
  <div class="tile"><span class="label">Payout of free cash flow</span><span class="value">{{ m.payout_fcf|pct(0) }}</span></div>
  {% if s.fair_value_mid is not none %}
  <div class="tile"><span class="label">Fair value (range)</span>
    <span class="value">{{ s.fair_value_mid|money(0) }}</span>
    <span class="sub">{{ s.fair_value_low|money(0) }} to {{ s.fair_value_high|money(0) }}</span></div>
  <div class="tile"><span class="label">Margin of safety</span><span class="value">{{ s.margin_of_safety|pct(0) }}</span></div>
  {% endif %}
</section>

<div id="daily-notice" class="note" role="status"></div>
<section aria-label="Charts"><div id="charts" class="chart-grid"></div></section>
<script type="application/json" id="charts-data">{{ charts|tojson }}</script>

{% if valuation %}
<section aria-labelledby="val-h">
  <h2 id="val-h">Valuation</h2>
  <div class="tiles">
    <div class="tile"><span class="label">P/E</span><span class="value">{{ m.pe|num(1) }}</span></div>
    <div class="tile"><span class="label">Price / free cash flow</span><span class="value">{{ m.p_fcf|num(1) }}</span></div>
    <div class="tile"><span class="label">Free cash flow yield</span><span class="value">{{ m.fcf_yield|pct }}</span></div>
    <div class="tile"><span class="label">Yield vs 5-year average</span><span class="value">{{ m.yield_vs_avg|pct(0) }}</span></div>
  </div>
  <p class="note">Earnings and cash flow are on the {{ 'last four quarters' if m.basis == 'ttm' else 'latest fiscal year' }}.
    Fair value is the Gordon growth model on the trailing dividend of {{ valuation.dps|money }}; growth is capped.</p>
  <div class="table-wrap"><table class="grid" aria-label="Fair value by required return and dividend growth">
    <caption>Fair value per share by required return (rows) and dividend growth (columns)</caption>
    <thead><tr><th>Required return</th>{% for g in valuation.growths %}<th class="num">{{ (g * 100)|round(0)|int }}% growth</th>{% endfor %}</tr></thead>
    <tbody>
    {% for r in valuation.returns %}{% set row = loop.index0 %}
      <tr><th>{{ (r * 100)|round(1) }}%</th>
        {% for g in valuation.growths %}{% set v = valuation.grid[row][loop.index0] %}
          <td class="num">{{ v|money(0) if v is not none else dash }}</td>{% endfor %}</tr>
    {% endfor %}
    </tbody>
  </table></div>
</section>
{% endif %}

{% if c.details %}
<section aria-labelledby="why-h">
  <h2 id="why-h">Why this score</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Metric</th><th>Pillar</th><th class="num">Value</th><th class="num">Band score</th><th class="num">Weight</th>
      <th class="num">Points of total</th><th class="num">Percentile</th></tr></thead>
    <tbody>
    {% for d in c.details %}
      <tr><td>{{ d.metric|metric_label }}</td><td>{{ pillar_labels[d.pillar] }}</td><td class="num">{{ d.metric|metric_value(d.value) }}</td>
        <td class="num">{{ d.band_score|score }}</td><td class="num">{{ d.weight|num(1) }}</td><td class="num">{{ d.contribution|num(1) }}</td>
        <td class="num">{{ d.percentile|score }}</td></tr>
    {% endfor %}
    </tbody>
  </table></div>
  <p class="note">The band score maps a value to 0–100 using the bands on the <a href="/methodology">methodology</a> page. Points of total add up to the score. The percentile ranks the value among all scored companies.</p>
</section>
{% endif %}
{% endblock %}
{% block scripts %}<script>window.dgiInitCompany && window.dgiInitCompany({{ c.ticker|tojson }});</script>{% endblock %}
```

- [ ] **Step C4: Run to verify it passes**

Run: `uv run pytest tests/test_web_company.py`
Expected: PASS (11 passed).

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Step C5: Render it and look at it**

The tests check data, not layout. Build a small cache, serve the app on a spare port with a stub price source, and screenshot it in dark and light. Tool and command are examples; the built-in browser or Claude in Chrome work as well.

```bash
uv run python - <<'PY'
import pathlib
from tests.web_fixtures import sample_cache
pathlib.Path("data").mkdir(exist_ok=True)
sample_cache(pathlib.Path("data/dgi.duckdb"))
PY
uv run python - <<'PY' &
import datetime as dt, pathlib, uvicorn
from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG
class Source:
    def daily(self, ticker):
        return [(dt.date(2024, 1, 1) + dt.timedelta(days=i), 40 + i * 0.02) for i in range(900)]
uvicorn.run(create_app(Settings(data_dir=pathlib.Path("data")), CFG, Source()), port=8765, log_level="warning")
PY
sleep 3
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CH" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=6000 --window-size=1300,2600 --screenshot=/tmp/dgi-company.png "http://127.0.0.1:8765/company/AAA"
"$CH" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=6000 --window-size=1300,1500 --screenshot=/tmp/dgi-screener.png "http://127.0.0.1:8765/?min_streak=0&max_payout_fcf=&min_cap_bn=0"
kill %1; rm -f data/dgi.duckdb
```
Look at both images and check, against the dataviz anti-pattern list: every chart has a title and, with two or more series, a visible legend; no axis tick label repeats; payout, leverage and coverage start at zero; the 100% and 5-year-average reference lines are labelled; bars have rounded tops on the baseline; nothing overlaps or overflows at 1300px; the table disclosure opens; the Theme button flips light/dark and the charts follow. Fix anything wrong in `app.css` or `charts.js` before committing. The same check at phone width (`--window-size=390,2600`) must show no horizontal page scroll.

- [ ] **Step C6: Commit**

```bash
git add src/dgi/web/templates/company.html tests/test_web_company.py
git commit -m "feat: company page with score explanation, valuation panel and charts"
```

---

### Task 22: `serve` command and the price source

Wires the UI to the real API for the daily charts and adds the command. Wiring only; the logic is unit-tested in Tasks 4, 20 and 21, and one PIPELINE test proves the whole path.

**Files:**
- Create: `src/dgi/client/series.py`
- Modify: `src/dgi/pipeline.py`, `src/dgi/report.py`, `src/dgi/cli.py` (replace each with the final version below), `tests/test_report.py` and `tests/test_cli_pipeline.py` (append)
- Test: `tests/test_client_series.py`

**Interfaces:**
- Consumes: `ApiClient.fetch_table`, `create_app`, `load_scoring_config`.
- Produces:
  - `ApiPriceSource(client).daily(ticker) -> list[tuple[date, float]]` (split-adjusted closes from `price_daily_adjusted`, oldest first, gaps skipped; `ApiUnavailable` when the API is down).
  - `pipeline.build_web_app(settings, client) -> Starlette`.
  - `report.exposure_warning(host) -> str | None` (a warning for any host other than `127.0.0.1`, `localhost`, `::1`: the UI has no authentication).
  - `dgi serve [--host H] [--port P]` (defaults `DGI_HOST` / `DGI_PORT`, else `127.0.0.1:8760`); module-level `cli.run_server(web_app, host, port)` is the seam the PIPELINE tests replace.

- [ ] **Step 1: Write the failing tests**

`tests/test_client_series.py` (UNIT: ApiPriceSource):
```python
import datetime as dt

import pyarrow as pa
import pytest

from dgi.client.series import ApiPriceSource
from dgi.errors import ApiUnavailable
from tests.fake_api import FakeGold

D = dt.date


def gold():
    return FakeGold({"price_daily_adjusted": pa.table({
        "ticker": ["KO", "KO", "KO", "PG"],
        "trade_date": pa.array([D(2024, 1, 3), D(2024, 1, 2), D(2024, 1, 4), D(2024, 1, 2)], pa.date32()),
        "close": [60.0, 59.0, None, 150.0],
        "split_adjusted_close": [30.0, 29.5, None, 150.0],
    })})


def test_daily_returns_one_tickers_adjusted_closes_oldest_first_and_skips_gaps():
    fake = gold()
    assert ApiPriceSource(fake.client()).daily("KO") == [(D(2024, 1, 2), 29.5), (D(2024, 1, 3), 30.0)]
    assert any("ticker=KO" in r and "price_daily_adjusted" in r for r in fake.requests)


def test_an_unknown_ticker_is_an_empty_series():
    assert ApiPriceSource(gold().client()).daily("ZZZ") == []


def test_an_api_that_is_down_raises_api_unavailable():
    fake = gold()
    fake.down = True
    with pytest.raises(ApiUnavailable):
        ApiPriceSource(fake.client()).daily("KO")
```

Append to `tests/test_report.py`:

`_aux/test_report_serve.py` (UNIT: exposure_warning):
```python
@pytest.mark.parametrize("host, warned", [("127.0.0.1", False), ("localhost", False), ("::1", False), ("0.0.0.0", True), ("192.168.1.5", True)])
def test_exposure_warning_only_for_addresses_beyond_this_machine(host, warned):
    from dgi.report import exposure_warning

    message = exposure_warning(host)
    assert (message is not None) == warned
    assert message is None or "no authentication" in message
```

Append to `tests/test_cli_pipeline.py`, add `from starlette.testclient import TestClient` to its imports, and add `"serve"` to the command list in `test_the_commands_are_registered`:

`_aux/test_cli_pipeline_serve.py` (PIPELINE: serve builds the app over the cache; one value traces through the pages; a clean failure):
```python
def test_serve_builds_the_app_over_the_cache_and_one_value_traces_through_the_web_pages(env, gold, monkeypatch):
    runner.invoke(cli.app, ["refresh"], env=env)
    seen = {}

    def fake_server(web_app, host, port):  # runs while `serve` still holds the API client, as uvicorn would
        seen.update(host=host, port=port)
        with TestClient(web_app) as client:
            page = client.get("/company/ACME").text
            seen["page"] = page
            seen["list"] = client.get("/").text
            seen["health"] = client.get("/health").json()
            seen["daily"] = client.get("/api/company/ACME/daily").json()

    monkeypatch.setattr(cli, "run_server", fake_server)
    result = runner.invoke(cli.app, ["serve", "--port", "9001"], env=env)
    assert result.exit_code == 0, result.output
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 9001)
    assert "Acme Beverages" in seen["page"] and "$60.00" in seen["page"] and "DGI score" in seen["page"]   # price 60.0 came from the API
    assert "ACME" in seen["list"] and seen["health"]["scored"] == 1
    assert seen["daily"]["available"] is True and seen["daily"]["charts"][0]["id"] == "price"             # one ticker pulled on demand


def test_serve_with_an_unreadable_scoring_config_exits_1(env, gold, monkeypatch):
    monkeypatch.setattr(cli, "run_server", lambda *a: pytest.fail("the server must not start"))
    result = runner.invoke(cli.app, ["serve"], env={**env, "DGI_SCORING_CONFIG": "/nonexistent/scoring.yaml"})
    assert result.exit_code == 1 and "error:" in result.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_client_series.py tests/test_report.py tests/test_cli_pipeline.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'dgi.client.series'`).

- [ ] **Step 3: Write the implementation**

`src/dgi/client/series.py`:
```python
"""One ticker's daily prices from the API, for the company page's daily charts."""

from __future__ import annotations

from datetime import date

from dgi.client.http import ApiClient


class ApiPriceSource:
    """Split-adjusted daily closes of one ticker. Raises the client's errors when the API cannot answer."""

    def __init__(self, client: ApiClient) -> None:
        self._client = client

    def daily(self, ticker: str) -> list[tuple[date, float]]:
        table = self._client.fetch_table("price_daily_adjusted", {"ticker": ticker})
        rows = zip(table["trade_date"].to_pylist(), table["split_adjusted_close"].to_pylist())
        return sorted((d, close) for d, close in rows if d is not None and close is not None)
```

Replace `src/dgi/pipeline.py`:

`src/dgi/pipeline.py`:
```python
"""Orchestration shared by the CLI and the PIPELINE tests. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
from starlette.applications import Starlette

from dgi.cache.build import compact_into, discard, new_path, prepare_work_file, swap_in
from dgi.cache.checks import CheckResult, run_cache_checks, verify_cache
from dgi.cache.meta import CacheMeta, RefreshPlan, next_meta, plan_refresh, read_meta, write_meta
from dgi.cache.status import CacheStatus, read_status
from dgi.client.contract import ContractStatus, fetch_contract
from dgi.client.http import ApiClient, Health
from dgi.client.pulls import build_pulls
from dgi.client.series import ApiPriceSource
from dgi.client.stage import stage_pull
from dgi.errors import CacheCheckError, CacheMissing
from dgi.fsutil import file_sha256
from dgi.metrics import MetricsResult, build_metrics, metrics_version
from dgi.results import RefreshResult
from dgi.scoring.config import ScoringConfig, load_scoring_config
from dgi.scoring.stage import ScoreResult, score_cache
from dgi.settings import Settings
from dgi.web.app import create_app


def pull_all(con: duckdb.DuckDBPyConnection, client: ApiClient, today: date) -> dict[str, int]:
    rows: dict[str, int] = {}
    for pull in build_pulls(today):
        rows[pull.table] = rows.get(pull.table, 0) + stage_pull(con, client.pages(pull.resource, pull.filters), pull.table)
    return rows


def build_candidate(
    settings: Settings,
    client: ApiClient,
    cfg: ScoringConfig,
    plan: RefreshPlan,
    previous: CacheMeta | None,
    health: Health,
    contract: ContractStatus,
    today: date,
    now: str,
    version: str,
    scoring_hash: str,
) -> tuple[Path, dict[str, int], MetricsResult | None, ScoreResult]:
    """Write the next cache next to the live one as `<live>.new`. The working file is always removed."""
    live = settings.cache_path
    work = prepare_work_file(live, plan)
    con = duckdb.connect(str(work))
    try:
        rows: dict[str, int] = {}
        metrics = None
        if plan.stage1:
            rows = pull_all(con, client, today)
            metrics = build_metrics(con, today, cfg.metrics)
        scores = score_cache(con, cfg, today)
        write_meta(con, next_meta(plan, previous, health.upstream_key, health.gold_built_at, contract.version, "; ".join(contract.warnings), version, scoring_hash, now))
        candidate = new_path(live)
        compact_into(con, candidate)
        return candidate, rows, metrics, scores
    finally:
        con.close()
        discard(work)


def run_refresh(settings: Settings, client: ApiClient, *, today: date, now: datetime, force: bool = False) -> RefreshResult:
    cfg = load_scoring_config(settings.scoring_path)
    health = client.health()
    contract = fetch_contract(client)
    live = settings.cache_path
    previous = read_meta(live)
    version = metrics_version(cfg.metrics)
    scoring_hash = file_sha256(settings.scoring_path)
    plan = plan_refresh(previous, health.upstream_key, contract.version, version, scoring_hash, force)
    if not (plan.stage1 or plan.score):
        return RefreshResult("up to date", plan.reason, health.upstream_key, {}, None, None, [], contract.warnings)
    candidate, rows, metrics, scores = build_candidate(
        settings, client, cfg, plan, previous, health, contract, today, now.isoformat(), version, scoring_hash
    )
    checks = verify_cache(candidate, live)
    failed = [c for c in checks if not c.passed]
    if failed:
        discard(candidate)
        raise CacheCheckError(
            "the new cache failed its checks and was not used; the previous cache keeps serving: "
            + "; ".join(f"{c.name}: {c.detail}" for c in failed)
        )
    swap_in(candidate, live)
    return RefreshResult("rebuilt" if plan.stage1 else "rescored", plan.reason, health.upstream_key, rows, metrics, scores, checks, contract.warnings)


def run_status(settings: Settings) -> CacheStatus:
    return read_status(settings.cache_path)


def run_check(settings: Settings) -> list[CheckResult]:
    path = settings.cache_path
    if not path.exists():
        raise CacheMissing(f"no cache at {path}; run `dgi refresh`")
    con = duckdb.connect(str(path), read_only=True)
    try:
        return run_cache_checks(con, None)
    finally:
        con.close()


def build_web_app(settings: Settings, client: ApiClient) -> Starlette:
    """The UI over the live cache; the company page's daily charts read one ticker at a time through `client`."""
    return create_app(settings, load_scoring_config(settings.scoring_path), ApiPriceSource(client))
```

Replace `src/dgi/report.py`:

`src/dgi/report.py`:
```python
"""CLI output formatting and the error-to-exit-code guard. No business logic."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

import typer

from dgi.cache.checks import CheckResult
from dgi.cache.status import CacheStatus
from dgi.errors import DgiError
from dgi.results import RefreshResult

T = TypeVar("T")


def guard(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except DgiError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def exposure_warning(host: str) -> str | None:
    """The UI has no authentication: say so when it listens beyond this machine."""
    if host in LOOPBACK:
        return None
    return f"warning: listening on {host}, not only on this machine, and the UI has no authentication"


def _reasons(counts: dict[str, int]) -> str:
    return ", ".join(f"{reason}={n}" for reason, n in sorted(counts.items())) or "none"


def describe_refresh(result: RefreshResult) -> str:
    lines = [f"{result.action}: {result.reason}", f"upstream: {result.upstream_key}"]
    if result.rows_pulled:
        lines.append("pulled: " + ", ".join(f"{t}={n}" for t, n in result.rows_pulled.items()))
    if result.metrics:
        m = result.metrics
        lines.append(f"metrics: {m.companies} companies, {m.dividend_payments} dividend payments, {m.with_streak} with a streak")
    if result.scores:
        s = result.scores
        lines.append(f"scores: {s.scored} scored of {s.companies}; not scored: {_reasons(s.not_scored)}")
    if result.checks:
        lines.append(f"checks: {sum(c.passed for c in result.checks)} of {len(result.checks)} passed")
    lines += [f"warning: {w}" for w in result.warnings]
    return "\n".join(lines)


def describe_status(status: CacheStatus, path: Path) -> str:
    m = status.meta
    return "\n".join([
        f"cache: {path}",
        f"built: {m.built_at}",
        f"upstream: {m.upstream_key}",
        f"contract: {m.contract_version}, metrics {m.metrics_version}, scoring {m.scoring_hash[:12]}",
        f"companies: {status.counts['company_dim']}, scored: {status.scored}",
        f"not scored: {_reasons(status.not_scored)}",
    ])


def print_checks(results: Iterable[CheckResult]) -> None:
    for r in results:
        typer.echo(f"{'PASS' if r.passed else 'FAIL'} {r.name}: {r.detail}")
```

Replace `src/dgi/cli.py`:

`src/dgi/cli.py`:
```python
"""Command line entry point. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import typer
import uvicorn
from starlette.applications import Starlette

from dgi.client.http import ApiClient
from dgi.pipeline import build_web_app, run_check, run_refresh, run_status
from dgi.report import describe_refresh, describe_status, exposure_warning, guard, print_checks
from dgi.settings import Settings

app = typer.Typer(help="Dividend growth screener over the investment platform API.", no_args_is_help=True)


def make_client(settings: Settings) -> ApiClient:
    return ApiClient(settings.api_url, timeout=settings.http_timeout, page_limit=settings.page_limit)


def current_date() -> date:
    return date.today()


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def run_server(web_app: Starlette, host: str, port: int) -> None:
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def refresh(
    force: bool = typer.Option(False, "--force", help="Ignore the change keys and rebuild everything."),
    api: str | None = typer.Option(None, "--api", help="Investment API base URL (default: DGI_INVEST_API_URL)."),
) -> None:
    """Pull from the API when upstream changed, build metrics, score, check, and swap the cache in."""
    settings = Settings.from_env()
    if api:
        settings = settings.model_copy(update={"api_url": api})
    with make_client(settings) as client:
        result = guard(lambda: run_refresh(settings, client, today=current_date(), now=current_time(), force=force))
    typer.echo(describe_refresh(result))


@app.command()
def status() -> None:
    """Show what the cache was built from and what it holds."""
    settings = Settings.from_env()
    typer.echo(describe_status(guard(lambda: run_status(settings)), settings.cache_path))


@app.command()
def check() -> None:
    """Run the quality checks on the live cache."""
    settings = Settings.from_env()
    results = guard(lambda: run_check(settings))
    print_checks(results)
    if not all(r.passed for r in results):
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host", help="Address to listen on (default: DGI_HOST or 127.0.0.1)."),
    port: int | None = typer.Option(None, "--port", help="Port (default: DGI_PORT or 8760)."),
) -> None:
    """Serve the read-only web UI over the cache."""
    settings = Settings.from_env()
    settings = settings.model_copy(update={k: v for k, v in (("host", host), ("port", port)) if v is not None})
    warning = exposure_warning(settings.host)
    if warning:
        typer.echo(warning, err=True)
    with make_client(settings) as client:
        web_app = guard(lambda: build_web_app(settings, client))
        run_server(web_app, settings.host, settings.port)
```

- [ ] **Step 4: Run to verify it passes, then the whole suite**

Run: `uv run pytest tests/test_client_series.py tests/test_report.py tests/test_cli_pipeline.py tests/test_architecture.py`
Expected: PASS (3 + 11 + 10 + 7 tests).

Run: `uv run pytest`
Expected: all tests pass (about 330).

- [ ] **Step 5: Commit**

```bash
git add src/dgi tests
git commit -m "feat: serve command with the API-backed price source for daily charts"
```
