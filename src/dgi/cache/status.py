"""What the live cache contains, for `dgi status` and the /health page."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from dgi.cache.checks import check_schema, row_counts
from dgi.cache.handle import open_readonly
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
    with open_readonly(path) as con:
        shape = check_schema(con)
        if not shape.passed:
            raise CacheMissing(f"the cache at {path} does not match this version ({shape.detail}); run `dgi refresh --force`")
        return read_status_from(con)
