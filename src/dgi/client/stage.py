"""Stream API pages into DuckDB staging tables so memory stays bounded."""

from __future__ import annotations

import re
from collections.abc import Iterable

import duckdb
import pyarrow as pa

STAGING_TABLES: tuple[str, ...] = (
    "stg_company", "stg_dividends", "stg_splits", "stg_income_annual", "stg_cash_annual", "stg_balance_annual",
    "stg_shares_cover", "stg_income_quarter", "stg_cash_quarter", "stg_prices_latest", "stg_prices_yearend",
)
_STAGING_NAME = re.compile(r"^stg_[a-z_]+$")


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()
    return row[0] > 0


def stage_pull(con: duckdb.DuckDBPyConnection, pages: Iterable[pa.Table], table: str) -> int:
    """Create `table` from the first page and append the rest; returns the number of rows staged."""
    if not _STAGING_NAME.match(table):
        raise ValueError(f"{table!r} is not a staging table name")
    rows = 0
    for page in pages:
        con.register("_stage_page", page)
        try:
            if table_exists(con, table):
                con.execute(f"INSERT INTO {table} SELECT * FROM _stage_page")
            else:
                con.execute(f"CREATE TABLE {table} AS SELECT * FROM _stage_page")
        finally:
            con.unregister("_stage_page")
        rows += page.num_rows
    return rows
