import duckdb
import pytest

from dgi.cache.handle import CacheHandle, open_readonly
from dgi.cache.build import new_path, swap_in
from dgi.errors import CacheMissing
from tests.cache_fixtures import cache_with, make_meta, persist


def tickers(handle):
    with handle.connection() as con:
        return None if con is None else [r[0] for r in con.execute("SELECT ticker FROM company_dim ORDER BY 1").fetchall()]


def test_no_file_means_no_connection(tmp_path):
    assert tickers(CacheHandle(tmp_path / "dgi.duckdb")) is None


def test_an_existing_cache_is_read(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    handle = CacheHandle(path)
    assert tickers(handle) == ["AAA"]
    assert tickers(handle) == ["AAA"]  # reuses the connection
    handle.close()


def test_a_swapped_cache_is_picked_up_without_a_restart(tmp_path):
    live = persist(cache_with({"OLD": ("2080", {})}), tmp_path / "dgi.duckdb")
    handle = CacheHandle(live)
    assert tickers(handle) == ["OLD"]
    swap_in(persist(cache_with({"NEW": ("2080", {})}), new_path(live)), live)
    assert tickers(handle) == ["NEW"]   # the old connection was closed first; DuckDB would otherwise keep serving the old file
    handle.close()


def test_a_cache_that_appears_later_is_picked_up_and_one_that_disappears_is_dropped(tmp_path):
    path = tmp_path / "dgi.duckdb"
    handle = CacheHandle(path)
    assert tickers(handle) is None
    persist(cache_with({"AAA": ("2080", {})}), path)
    assert tickers(handle) == ["AAA"]
    path.unlink()
    assert tickers(handle) is None
    handle.close()


def test_a_corrupt_file_reads_as_no_cache(tmp_path):
    path = tmp_path / "dgi.duckdb"
    path.write_bytes(b"garbage" * 200)
    handle = CacheHandle(path)
    assert tickers(handle) is None
    handle.close()


def test_open_readonly_says_to_refresh_when_there_is_no_cache(tmp_path):
    with pytest.raises(CacheMissing, match="run `dgi refresh`"):
        with open_readonly(tmp_path / "dgi.duckdb"):
            pass


def test_open_readonly_turns_a_file_that_is_not_a_database_into_cache_missing(tmp_path):
    corrupt = tmp_path / "dgi.duckdb"
    corrupt.write_bytes(b"garbage" * 200)
    with pytest.raises(CacheMissing, match="cannot open.*refresh --force"):
        with open_readonly(corrupt):
            pass


def test_open_readonly_turns_a_storage_failure_while_reading_into_cache_missing(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    with pytest.raises(CacheMissing, match="unreadable.*refresh --force"):
        with open_readonly(path):
            raise duckdb.IOException("disk read failed")


def test_open_readonly_lets_a_sql_bug_through_as_itself(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    with pytest.raises(duckdb.CatalogException):
        with open_readonly(path) as con:
            con.execute("SELECT nope FROM no_such_table")
    with pytest.raises(duckdb.BinderException):
        with open_readonly(path) as con:
            con.execute("SELECT no_such_column FROM company_dim")


def test_open_readonly_closes_the_connection(tmp_path):
    path = persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb")
    with open_readonly(path) as con:
        pass
    with pytest.raises(duckdb.ConnectionException):
        con.execute("SELECT 1")
