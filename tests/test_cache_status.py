import duckdb
import pytest

from dgi.cache.status import read_status
from dgi.errors import CacheMissing
from tests.cache_fixtures import make_meta, persist, scored_cache


def test_status_reports_meta_counts_and_the_reasons_for_not_scoring(tmp_path):
    con = scored_cache({"AAA": ("2080", {}), "BANK": ("6021", {}), "NEWC": ("2080", {"years_history": 2})})
    status = read_status(persist(con, tmp_path / "dgi.duckdb", make_meta()))
    assert status.meta == make_meta()
    assert status.counts["company_dim"] == 3 and status.scored == 1
    assert status.not_scored == {"financial_or_reit": 1, "short_dividend_history": 1}


def test_a_missing_cache_says_to_run_refresh(tmp_path):
    with pytest.raises(CacheMissing, match="run `dgi refresh`"):
        read_status(tmp_path / "dgi.duckdb")


def test_a_cache_with_the_wrong_columns_is_cache_missing_not_a_sql_error(tmp_path):
    from dgi import schema
    path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(path))
    schema.create_tables(con)
    con.execute("ALTER TABLE scores DROP COLUMN status")
    con.close()
    with pytest.raises(CacheMissing, match="does not match.*refresh --force"):
        read_status(path)


def test_a_corrupt_file_and_a_cache_without_meta_are_cache_missing_with_a_way_out(tmp_path):
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_bytes(b"garbage" * 200)
    with pytest.raises(CacheMissing, match="refresh --force"):
        read_status(corrupt)
    bare = tmp_path / "bare.duckdb"
    duckdb.connect(str(bare)).close()
    with pytest.raises(CacheMissing, match="refresh --force"):
        read_status(bare)
    con = duckdb.connect(str(tmp_path / "nometa.duckdb"))
    from dgi import schema
    schema.create_tables(con)
    con.close()
    with pytest.raises(CacheMissing, match="no meta row"):
        read_status(tmp_path / "nometa.duckdb")
