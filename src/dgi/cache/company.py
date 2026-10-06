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
