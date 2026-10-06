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
