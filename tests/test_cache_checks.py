import duckdb
import pytest

from dgi import schema
from dgi.cache.build import new_path
from dgi.cache.checks import (
    check_no_duplicates, check_row_counts, check_schema, check_scored_share, check_streak_bounds,
    check_yield_outliers, live_counts, row_counts, run_cache_checks, verify_cache,
)
from tests.cache_fixtures import cache_with, persist, scored_cache

ONE = {"AAA": ("2080", {})}


def test_a_healthy_cache_passes_every_check():
    results = run_cache_checks(scored_cache({**ONE, "BANK": ("6021", {})}) , None)
    assert [r.name for r in results] == ["schema", "row_counts", "no_duplicates", "streak_bounds", "yield_outliers", "scored_share"]
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_schema_check_names_the_tables_that_differ():
    con = scored_cache(ONE)
    con.execute("ALTER TABLE flags DROP COLUMN text")
    result = check_schema(con)
    assert not result.passed and "flags" in result.detail
    assert [r.name for r in run_cache_checks(con, None)] == ["schema"]  # later checks would only fail confusingly


def test_row_counts_fail_when_a_table_collapses_against_the_previous_cache():
    counts = {"company_dim": 100, "metrics_current": 100, "dividend_annual": 1000, "fundamentals_annual": 1000, "scores": 100}
    ok = check_row_counts(counts, dict(counts))
    assert ok.passed
    shrunk = {**counts, "dividend_annual": 700}
    bad = check_row_counts(shrunk, counts)
    assert not bad.passed and "dividend_annual 1000 -> 700" in bad.detail
    assert check_row_counts({**counts, "dividend_annual": 800}, counts).passed  # exactly at the limit


def test_row_counts_without_a_previous_cache_only_require_companies():
    assert check_row_counts({"company_dim": 3, "metrics_current": 3, "dividend_annual": 0, "fundamentals_annual": 0, "scores": 3}, None).passed
    empty = {"company_dim": 0, "metrics_current": 0, "dividend_annual": 0, "fundamentals_annual": 0, "scores": 0}
    assert not check_row_counts(empty, None).passed


def test_duplicate_keys_fail():
    con = scored_cache(ONE)
    schema.insert_rows(con, "dividend_annual", [{"ticker": "AAA", "year": 2024}, {"ticker": "AAA", "year": 2024}])
    result = check_no_duplicates(con)
    assert not result.passed and "dividend_annual" in result.detail


def test_a_streak_longer_than_the_history_fails():
    assert check_streak_bounds(scored_cache(ONE)).passed
    bad = scored_cache({"AAA": ("2080", {"streak": 12, "years_history": 12})})
    assert not check_streak_bounds(bad).passed


def test_yield_outliers_fail_only_when_many_scored_companies_look_wrong():
    fine = scored_cache({f"T{i}": ("2080", {"div_yield": 0.03}) for i in range(20)})
    assert check_yield_outliers(fine).passed
    one_in_twenty = scored_cache({**{f"T{i}": ("2080", {}) for i in range(19)}, "WILD": ("2080", {"div_yield": 0.6})})
    assert check_yield_outliers(one_in_twenty).passed        # 5% is allowed
    many = scored_cache({**{f"T{i}": ("2080", {}) for i in range(10)}, **{f"W{i}": ("2080", {"div_yield": 0.6}) for i in range(3)}})
    result = check_yield_outliers(many)
    assert not result.passed and "3 of 13" in result.detail


def test_scored_share_must_be_plausible():
    assert check_scored_share(scored_cache({**ONE, "BANK": ("6021", {})})).passed
    everything = check_scored_share(scored_cache(ONE))   # 100% scored cannot be right for a whole market
    assert not everything.passed and "expected between" in everything.detail
    none_scored = scored_cache({"BANK": ("6021", {})})
    assert not check_scored_share(none_scored).passed
    nothing = scored_cache({})
    result = check_scored_share(nothing)
    assert not result.passed and "no companies" in result.detail


def market(n: int) -> dict:
    """n scored companies plus two banks, so the scored share stays plausible."""
    return {**{f"T{i}": ("2080", {}) for i in range(n)}, "B1": ("6021", {}), "B2": ("6021", {})}


def test_verify_cache_compares_with_the_live_cache(tmp_path):
    live = persist(scored_cache(market(10)), tmp_path / "dgi.duckdb")
    smaller = persist(scored_cache(market(5)), new_path(live))
    failed = [r for r in verify_cache(smaller, live) if not r.passed]
    assert [r.name for r in failed] == ["row_counts"]


def test_verify_cache_without_a_live_cache_or_with_a_corrupt_one_still_checks_the_candidate(tmp_path):
    candidate = persist(scored_cache(market(5)), tmp_path / "dgi.duckdb.new")
    assert all(r.passed for r in verify_cache(candidate, tmp_path / "missing.duckdb"))
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_bytes(b"garbage" * 200)
    assert all(r.passed for r in verify_cache(candidate, corrupt))


def test_row_counts_reads_every_counted_table():
    assert set(row_counts(scored_cache(ONE))) == {"company_dim", "metrics_current", "dividend_annual", "fundamentals_annual", "scores"}


def test_live_counts_is_none_for_a_missing_or_unreadable_cache(tmp_path):
    assert live_counts(tmp_path / "missing.duckdb") is None
    bare = tmp_path / "bare.duckdb"
    duckdb.connect(str(bare)).close()
    assert live_counts(bare) is None  # a database without the cache tables
    good = persist(scored_cache(ONE), tmp_path / "dgi.duckdb")
    assert live_counts(good)["company_dim"] == 1
