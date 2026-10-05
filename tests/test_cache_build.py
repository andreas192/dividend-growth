import duckdb

from dgi.cache.build import compact_into, discard, new_path, prepare_work_file, swap_in, work_path
from dgi.cache.meta import RefreshPlan
from dgi.schema import PERSISTED_TABLES, table_columns
from tests.cache_fixtures import cache_with, persist

FULL = RefreshPlan(True, True, "x")
SCORE_ONLY = RefreshPlan(False, True, "x")


def test_a_full_rebuild_starts_from_nothing_and_clears_leftovers(tmp_path):
    live = tmp_path / "data" / "dgi.duckdb"
    live.parent.mkdir()
    live.write_text("live")
    for leftover in (work_path(live), new_path(live), work_path(live).with_name("dgi.duckdb.work.wal"), new_path(live).with_name("dgi.duckdb.new.wal")):
        leftover.write_text("crashed run")
    work = prepare_work_file(live, FULL)
    assert work == work_path(live) and not work.exists() and not new_path(live).exists()
    assert not list(live.parent.glob("*.wal")) and live.read_text() == "live"


def test_a_score_only_rebuild_starts_from_a_copy_of_the_live_cache(tmp_path):
    live = tmp_path / "dgi.duckdb"
    live.write_bytes(b"live bytes")
    work = prepare_work_file(live, SCORE_ONLY)
    assert work.read_bytes() == b"live bytes" and live.read_bytes() == b"live bytes"


def test_the_data_directory_is_created_when_missing(tmp_path):
    live = tmp_path / "new" / "dir" / "dgi.duckdb"
    prepare_work_file(live, FULL)
    assert live.parent.is_dir()


def test_compaction_copies_only_the_persisted_tables(tmp_path):
    con = cache_with({"AAA": ("2080", {})})
    con.execute("CREATE TABLE stg_company AS SELECT 1 AS cik")
    con.execute("CREATE TABLE scratch AS SELECT 1 AS x")
    from dgi.cache.meta import write_meta
    from tests.cache_fixtures import make_meta
    write_meta(con, make_meta())
    out = tmp_path / "new.duckdb"
    compact_into(con, out)
    con.close()
    check = duckdb.connect(str(out), read_only=True)
    tables = {r[0] for r in check.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert tables == set(PERSISTED_TABLES)
    assert check.execute("SELECT ticker FROM metrics_current").fetchall() == [("AAA",)]
    assert table_columns(check, "scores")
    check.close()
    assert not out.with_name("new.duckdb.wal").exists()


def test_swap_in_replaces_the_live_file_and_leaves_no_candidate(tmp_path):
    live = persist(cache_with({"OLD": ("2080", {})}), tmp_path / "dgi.duckdb")
    candidate = persist(cache_with({"NEW": ("2080", {})}), new_path(live))
    swap_in(candidate, live)
    assert not candidate.exists()
    con = duckdb.connect(str(live), read_only=True)
    assert con.execute("SELECT ticker FROM company_dim").fetchall() == [("NEW",)]
    con.close()


def test_discard_removes_a_candidate_and_its_wal_and_tolerates_absence(tmp_path):
    path = tmp_path / "x.new"
    path.write_text("a")
    path.with_name("x.new.wal").write_text("b")
    discard(path)
    discard(path)
    assert not path.exists() and not path.with_name("x.new.wal").exists()
