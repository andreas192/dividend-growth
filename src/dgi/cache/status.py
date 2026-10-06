"""What the live cache contains, for `dgi status` and the /health page."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from dgi.cache.checks import row_counts
from dgi.cache.meta import CacheMeta, read_meta_from
from dgi.errors import CacheMissing


@dataclass(frozen=True)
class CacheStatus:
    meta: CacheMeta
    counts: dict[str, int]
    scored: int
    not_scored: dict[str, int]


def read_status_from(con: duckdb.DuckDBPyConnection) -> CacheStatus:
    meta = read_meta_from(con)
    if meta is None:
        raise CacheMissing("the cache has no meta row; run `dgi refresh --force`")
    scored = con.execute("SELECT count(*) FROM scores WHERE status = 'scored'").fetchone()[0]
    reasons = con.execute("SELECT reason, count(*) FROM scores WHERE status = 'not_scored' GROUP BY reason ORDER BY reason").fetchall()
    return CacheStatus(meta, row_counts(con), scored, {reason: n for reason, n in reasons})


def read_status(path: Path) -> CacheStatus:
    if not path.exists():
        raise CacheMissing(f"no cache at {path}; run `dgi refresh`")
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise CacheMissing(f"cannot open the cache at {path}: {exc}; run `dgi refresh --force`") from exc
    try:
        return read_status_from(con)
    except duckdb.Error as exc:
        raise CacheMissing(f"the cache at {path} is unreadable: {exc}; run `dgi refresh --force`") from exc
    finally:
        con.close()
