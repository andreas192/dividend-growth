import duckdb
import pytest

from dgi.cache.meta import RefreshPlan, next_meta, plan_refresh, read_meta, write_meta
from tests.cache_fixtures import cache_with, make_meta, persist

KEY, V, M, S = "hash-1|2026-10-04T06:00:00+00:00", "v1", "1:abc", "s1"


def test_write_then_read_round_trips_through_a_file(tmp_path):
    meta = make_meta()
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb", meta)
    assert read_meta(path) == meta


def test_no_usable_cache_reads_as_none(tmp_path):
    assert read_meta(tmp_path / "missing.duckdb") is None
    garbage = tmp_path / "garbage.duckdb"
    garbage.write_bytes(b"this is not a duckdb file" * 100)
    assert read_meta(garbage) is None
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    assert read_meta(empty) is None  # no meta table
    con = duckdb.connect(str(tmp_path / "nometa.duckdb"))
    con.execute("CREATE TABLE meta (upstream_key VARCHAR, upstream_built_at VARCHAR, contract_version VARCHAR, metrics_version VARCHAR, scoring_hash VARCHAR, built_at VARCHAR, contract_warnings VARCHAR)")
    con.close()
    assert read_meta(tmp_path / "nometa.duckdb") is None  # table without a row


def test_write_meta_replaces_the_row():
    con = duckdb.connect()
    write_meta(con, make_meta(scoring_hash="a"))
    write_meta(con, make_meta(scoring_hash="b"))
    assert con.execute("SELECT scoring_hash FROM meta").fetchall() == [("b",)]


@pytest.mark.parametrize(
    "meta, args, expected",
    [
        (None, (KEY, V, M, S, False), RefreshPlan(True, True, "no usable cache")),
        (make_meta(), (KEY, V, M, S, True), RefreshPlan(True, True, "forced")),
        (make_meta(), ("other|time", V, M, S, False), RefreshPlan(True, True, "upstream data changed")),
        (make_meta(), (KEY, "v2", M, S, False), RefreshPlan(True, True, "contract version changed")),
        (make_meta(), (KEY, V, "2:abc", S, False), RefreshPlan(True, True, "metrics code or history settings changed")),
        (make_meta(), (KEY, V, M, "s2", False), RefreshPlan(False, True, "scoring config changed")),
        (make_meta(), (KEY, V, M, S, False), RefreshPlan(False, False, "up to date")),
    ],
)
def test_plan_refresh_picks_the_smallest_stage_that_has_work(meta, args, expected):
    assert plan_refresh(meta, *args) == expected


def test_a_data_change_beats_a_scoring_change():
    plan = plan_refresh(make_meta(), "new|key", V, M, "s2", False)
    assert plan.stage1 and plan.score


NOW = "2026-10-06T07:00:00+00:00"
NEW = ("h2|2026-10-06T06:00:00+00:00", "2026-10-06T06:00:00+00:00", "v1", "contract v1 is deprecated, sunset 2027-01-01")  # upstream key, build time, contract version, warnings


def test_next_meta_after_a_full_rebuild_takes_the_new_upstream_and_contract():
    got = next_meta(RefreshPlan(True, True, "x"), make_meta(), *NEW, "1:new", "s9", NOW)
    assert (got.upstream_key, got.upstream_built_at, got.contract_version, got.metrics_version, got.scoring_hash, got.built_at) == (
        "h2|2026-10-06T06:00:00+00:00", "2026-10-06T06:00:00+00:00", "v1", "1:new", "s9", NOW)
    assert got.contract_warnings == "contract v1 is deprecated, sunset 2027-01-01"


def test_next_meta_after_a_score_only_rebuild_keeps_the_data_identity():
    previous = make_meta()
    got = next_meta(RefreshPlan(False, True, "x"), previous, *NEW, "1:new", "s9", NOW)
    assert got.upstream_key == previous.upstream_key and got.metrics_version == previous.metrics_version
    assert got.scoring_hash == "s9" and got.built_at == NOW and got.contract_warnings == previous.contract_warnings


def test_next_meta_without_a_previous_cache_uses_the_current_values():
    got = next_meta(RefreshPlan(False, True, "x"), None, *NEW, "1:new", "s9", NOW)
    assert got.upstream_key == "h2|2026-10-06T06:00:00+00:00"
