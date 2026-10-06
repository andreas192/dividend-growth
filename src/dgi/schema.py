"""Table definitions for the cache (dgi.duckdb): the single source of truth for column names and types."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import duckdb
import pyarrow as pa

_D, _V, _I, _B, _DATE = "DOUBLE", "VARCHAR", "INTEGER", "BOOLEAN", "DATE"

_FUNDAMENTALS = (
    "revenue net_income operating_income eps_diluted shares_diluted interest_expense cfo capex dividends_paid "
    "depreciation_amortization fcf equity current_assets current_liabilities cash short_term_investments debt "
    "net_debt ebitda op_margin payout_earnings payout_fcf fcf_per_share"
).split()

COLUMN_DEFS: dict[str, list[tuple[str, str]]] = {
    "company_dim": [("ticker", _V), ("cik", "BIGINT"), ("name", _V), ("sic", _V), ("sic_description", _V)],
    "dividend_payment": [("ticker", _V), ("ex_date", _DATE), ("year", _I), ("amount_adj", _D), ("is_special", _B)],
    "dividend_annual": [
        ("ticker", _V), ("year", _I), ("dps", _D), ("n_payments", _I), ("special_total", _D), ("complete", _B),
    ],
    "price_yearend": [("ticker", _V), ("year", _I), ("close_adj", _D)],
    "fundamentals_annual": [("ticker", _V), ("fiscal_year", _I), ("period_end", _DATE)] + [(c, _D) for c in _FUNDAMENTALS]
    + [("dividends_estimated", _B)],
    "metrics_current": [
        ("ticker", _V), ("price", _D), ("price_date", _DATE), ("market_cap", _D), ("dividend_ttm", _D),
        ("div_yield", _D), ("div_yield_avg_5y", _D), ("yield_vs_avg", _D), ("streak", _I), ("no_cut_streak", _I),
        ("dgr_1", _D), ("dgr_3", _D), ("dgr_5", _D), ("dgr_10", _D), ("years_history", _I), ("payment_frequency", _I),
        ("cut_years_5y", _I), ("irregular_payments", _B), ("special_count_5y", _I), ("suspect_dividend_count", _I),
        ("latest_fy", _I), ("fy_period_end", _DATE), ("basis", _V), ("eps_basis", _D), ("fcf_ps_basis", _D),
        ("payout_earnings", _D), ("payout_earnings_5y", _D), ("payout_fcf", _D), ("payout_fcf_5y", _D),
        ("interest_coverage", _D), ("net_debt_ebitda", _D), ("current_ratio", _D), ("positive_earnings_years", _I),
        ("rev_cagr_5", _D), ("eps_cagr_5", _D), ("fcf_ps_cagr_5", _D), ("roe", _D), ("op_margin_std", _D),
        ("share_trend_5", _D), ("pe", _D), ("p_fcf", _D), ("fcf_yield", _D), ("fcf_latest", _D), ("net_income_latest", _D),
    ],
    "scores": [
        ("ticker", _V), ("status", _V), ("reason", _V), ("sector_group", _V), ("dividend", _D), ("safety", _D),
        ("growth", _D), ("valuation", _D), ("coverage", _D), ("total", _D), ("fair_value_low", _D),
        ("fair_value_mid", _D), ("fair_value_high", _D), ("margin_of_safety", _D),
    ],
    "score_detail": [
        ("ticker", _V), ("metric", _V), ("pillar", _V), ("value", _D), ("band_score", _D), ("weight", _D),
        ("contribution", _D), ("percentile", _D),
    ],
    "flags": [("ticker", _V), ("code", _V), ("severity", _V), ("text", _V)],
    "meta": [
        ("upstream_key", _V), ("upstream_built_at", _V), ("contract_version", _V), ("metrics_version", _V),
        ("scoring_hash", _V), ("built_at", _V), ("contract_warnings", _V),
    ],
}

PERSISTED_TABLES: tuple[str, ...] = tuple(COLUMN_DEFS)

_ARROW_TYPES = {
    "VARCHAR": pa.string(), "DOUBLE": pa.float64(), "INTEGER": pa.int32(), "BIGINT": pa.int64(),
    "DATE": pa.date32(), "BOOLEAN": pa.bool_(),
}


def columns(name: str) -> list[str]:
    return [c for c, _ in COLUMN_DEFS[name]]


def ddl(name: str) -> str:
    body = ", ".join(f"{c} {t}" for c, t in COLUMN_DEFS[name])
    return f"CREATE TABLE {name} ({body})"


def create_tables(con: duckdb.DuckDBPyConnection, names: Iterable[str] | None = None) -> None:
    for name in names if names is not None else COLUMN_DEFS:
        con.execute(ddl(name))


def recreate_tables(con: duckdb.DuckDBPyConnection, names: Iterable[str]) -> None:
    for name in names:
        con.execute(f"DROP TABLE IF EXISTS {name}")
        con.execute(ddl(name))


def table_columns(con: duckdb.DuckDBPyConnection, name: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position", [name]
    ).fetchall()
    return [r[0] for r in rows]


def arrow_schema(name: str) -> pa.Schema:
    return pa.schema([(c, _ARROW_TYPES[t]) for c, t in COLUMN_DEFS[name]])


def insert_rows(con: duckdb.DuckDBPyConnection, name: str, records: Sequence[Mapping[str, Any]]) -> int:
    """Insert dict rows by column name; columns a record omits become NULL. Returns the row count."""
    table = pa.Table.from_pylist(list(records), schema=arrow_schema(name))
    con.register("_insert_rows", table)
    try:
        con.execute(f"INSERT INTO {name} BY NAME SELECT * FROM _insert_rows")
    finally:
        con.unregister("_insert_rows")
    return table.num_rows
