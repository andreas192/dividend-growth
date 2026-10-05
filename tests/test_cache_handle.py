import duckdb

from dgi.cache.handle import CacheHandle
from dgi.cache.build import new_path, swap_in
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
