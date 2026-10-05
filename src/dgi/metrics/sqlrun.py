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
