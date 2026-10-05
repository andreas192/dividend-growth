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
    if size != PAGE_SIZE:
        normalized["size"] = str(size)
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
