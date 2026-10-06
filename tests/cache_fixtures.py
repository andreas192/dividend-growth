"""Hand-built cache contents for the scoring, cache and web tests: no API, no network."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb

from dgi import schema
from dgi.cache.build import compact_into
from dgi.cache.meta import CacheMeta, write_meta
from dgi.scoring.config import ScoringConfig, load_scoring_config
from dgi.scoring.stage import score_cache

TODAY = dt.date(2026, 10, 5)
FROZEN_SCORING = Path(__file__).resolve().parent / "frozen" / "scoring.yaml"  # a copy of config/scoring.yaml: editing the owner's file must not move the tests
CFG: ScoringConfig = load_scoring_config(FROZEN_SCORING)

# A healthy dividend grower's metrics_current row; tests override single values.
GOOD: dict[str, Any] = {
    "price": 60.0, "years_history": 12, "fy_period_end": dt.date(2025, 12, 31), "dividend_ttm": 1.8, "market_cap": 5e9,
    "div_yield": 0.03, "div_yield_avg_5y": 0.028, "yield_vs_avg": 0.05, "streak": 11, "no_cut_streak": 11,
    "dgr_1": 0.05, "dgr_3": 0.05, "dgr_5": 0.05, "dgr_10": 0.05, "payment_frequency": 4, "cut_years_5y": 0,
    "special_count_5y": 0, "suspect_dividend_count": 0, "irregular_payments": False, "latest_fy": 2025, "basis": "fy",
    "eps_basis": 3.3, "fcf_ps_basis": 3.0, "payout_earnings": 0.5, "payout_earnings_5y": 0.5, "payout_fcf": 0.6,
    "payout_fcf_5y": 0.6, "interest_coverage": 15.0, "net_debt_ebitda": 1.0, "current_ratio": 2.0,
    "positive_earnings_years": 10, "rev_cagr_5": 0.04, "eps_cagr_5": 0.06, "fcf_ps_cagr_5": 0.06, "roe": 0.2,
    "op_margin_std": 0.01, "share_trend_5": -0.01, "pe": 18.0, "p_fcf": 20.0, "fcf_yield": 0.05,
    "fcf_latest": 100.0, "net_income_latest": 160.0,
}


def cache_with(companies: dict[str, tuple[str, dict[str, Any]]]) -> duckdb.DuckDBPyConnection:
    """companies: ticker -> (sic, overrides of GOOD). Creates every cache table; fills company_dim and metrics_current."""
    con = duckdb.connect()
    schema.create_tables(con)
    schema.insert_rows(con, "company_dim", [{"ticker": t, "cik": i, "name": f"{t} Inc", "sic": sic} for i, (t, (sic, _)) in enumerate(companies.items())])
    schema.insert_rows(con, "metrics_current", [{"ticker": t, **{**GOOD, **over}} for t, (_, over) in companies.items()])
    return con


def scored_cache(companies: dict[str, tuple[str, dict[str, Any]]]) -> duckdb.DuckDBPyConnection:
    con = cache_with(companies)
    score_cache(con, CFG, TODAY)
    return con


def make_meta(**overrides: Any) -> CacheMeta:
    values: dict[str, Any] = dict(
        upstream_key="hash-1|2026-10-04T06:00:00+00:00", upstream_built_at="2026-10-04T06:00:00+00:00",
        contract_version="v1", metrics_version="1:abc", scoring_hash="s1", built_at="2026-10-05T07:00:00+00:00", contract_warnings="",
    )
    return CacheMeta(**{**values, **overrides})


def persist(con: duckdb.DuckDBPyConnection, path: Path, meta: CacheMeta | None = None) -> Path:
    """Write `con`'s cache tables (plus meta) to a cache file, as the pipeline's compaction does."""
    write_meta(con, meta or make_meta())
    compact_into(con, path)
    return path
