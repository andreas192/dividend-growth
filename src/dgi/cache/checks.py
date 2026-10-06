"""Quality checks on a cache file. A new cache must pass these before it replaces the live one."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import duckdb

from dgi import schema

COUNTED_TABLES = ("company_dim", "metrics_current", "dividend_annual", "fundamentals_annual", "scores")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {name: con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in COUNTED_TABLES}


def check_schema(con: duckdb.DuckDBPyConnection) -> CheckResult:
    problems = [
        name for name in schema.PERSISTED_TABLES if schema.table_columns(con, name) != schema.columns(name)
    ]
    if problems:
        return CheckResult("schema", False, "missing or different columns in: " + ", ".join(problems))
    return CheckResult("schema", True, f"{len(schema.PERSISTED_TABLES)} tables as declared")


def check_row_counts(counts: Mapping[str, int], previous: Mapping[str, int] | None, min_ratio: float = 0.8) -> CheckResult:
    if counts["company_dim"] == 0:
        return CheckResult("row_counts", False, "the cache has no companies")
    if previous is None:
        return CheckResult("row_counts", True, "no previous cache to compare with")
    collapsed = [
        f"{name} {previous[name]} -> {counts[name]}"
        for name in COUNTED_TABLES
        if previous.get(name, 0) > 0 and counts[name] < previous[name] * min_ratio
    ]
    if collapsed:
        return CheckResult("row_counts", False, "rows collapsed against the previous cache: " + "; ".join(collapsed))
    return CheckResult("row_counts", True, "no table lost more than " + f"{1 - min_ratio:.0%} of its rows")


def check_no_duplicates(con: duckdb.DuckDBPyConnection) -> CheckResult:
    keys = {
        "company_dim": "ticker", "metrics_current": "ticker", "scores": "ticker",
        "dividend_annual": "ticker, year", "fundamentals_annual": "ticker, fiscal_year",
    }
    duplicated = [
        table for table, key in keys.items()
        if con.execute(f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING count(*) > 1)").fetchone()[0] > 0
    ]
    if duplicated:
        return CheckResult("no_duplicates", False, "duplicate keys in: " + ", ".join(duplicated))
    return CheckResult("no_duplicates", True, "keys are unique")


def check_streak_bounds(con: duckdb.DuckDBPyConnection) -> CheckResult:
    bad = con.execute(
        "SELECT count(*) FROM metrics_current WHERE streak > COALESCE(years_history, 0) - 1 OR no_cut_streak > COALESCE(years_history, 0) - 1"
    ).fetchone()[0]
    if bad:
        return CheckResult("streak_bounds", False, f"{bad} companies have a streak longer than their history")
    return CheckResult("streak_bounds", True, "no streak exceeds its history")


def check_yield_outliers(con: duckdb.DuckDBPyConnection, max_yield: float = 0.25, max_share: float = 0.05) -> CheckResult:
    scored, outliers = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE m.div_yield > ?) FROM scores s JOIN metrics_current m USING (ticker) WHERE s.status = 'scored'",
        [max_yield],
    ).fetchone()
    detail = f"{outliers} of {scored} scored companies yield more than {max_yield:.0%}"
    if scored and outliers / scored > max_share:
        return CheckResult("yield_outliers", False, detail + f" (over {max_share:.0%}: the price or dividend data looks wrong)")
    return CheckResult("yield_outliers", True, detail)


def check_scored_share(con: duckdb.DuckDBPyConnection, min_share: float = 0.02, max_share: float = 0.90) -> CheckResult:
    total, scored = con.execute("SELECT count(*), count(*) FILTER (WHERE status = 'scored') FROM scores").fetchone()
    if total == 0:
        return CheckResult("scored_share", False, "no companies were scored or listed")
    share = scored / total
    detail = f"{scored} of {total} companies scored ({share:.1%})"
    if not min_share <= share <= max_share:
        return CheckResult("scored_share", False, detail + f"; expected between {min_share:.0%} and {max_share:.0%}")
    return CheckResult("scored_share", True, detail)


def run_cache_checks(con: duckdb.DuckDBPyConnection, previous_counts: Mapping[str, int] | None = None) -> list[CheckResult]:
    shape = check_schema(con)
    if not shape.passed:
        return [shape]
    return [
        shape,
        check_row_counts(row_counts(con), previous_counts),
        check_no_duplicates(con),
        check_streak_bounds(con),
        check_yield_outliers(con),
        check_scored_share(con),
    ]


def live_counts(live: Path) -> dict[str, int] | None:
    """Row counts of the live cache, or None when there is none to compare with (missing or unreadable)."""
    if not live.exists():
        return None
    try:
        con = duckdb.connect(str(live), read_only=True)
    except duckdb.Error:
        return None
    try:
        return row_counts(con)
    except duckdb.Error:
        return None
    finally:
        con.close()


def verify_cache(candidate: Path, live: Path) -> list[CheckResult]:
    """Check `candidate`, comparing its row counts with the live cache when there is one."""
    con = duckdb.connect(str(candidate), read_only=True)
    try:
        return run_cache_checks(con, live_counts(live))
    finally:
        con.close()
