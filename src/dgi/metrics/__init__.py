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
