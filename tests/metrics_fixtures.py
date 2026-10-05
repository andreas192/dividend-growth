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
