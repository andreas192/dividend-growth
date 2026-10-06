"""What the cache was built from (meta table) and the decision whether a refresh has work to do."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from dgi.schema import columns, insert_rows, recreate_tables


@dataclass(frozen=True)
class CacheMeta:
    upstream_key: str
    upstream_built_at: str | None
    contract_version: str
    metrics_version: str
    scoring_hash: str
    built_at: str  # ISO 8601, UTC
    contract_warnings: str = ""  # deprecation notices seen at the last full rebuild, joined with '; '


@dataclass(frozen=True)
class RefreshPlan:
    stage1: bool   # pull from the API and rebuild the metrics
    score: bool    # rescore from the cache
    reason: str


@dataclass(frozen=True)
class Upstream:
    """What the data source says about itself: asked of the API, or taken from the cache when the API cannot be asked."""
    key: str
    built_at: str | None
    contract_version: str
    contract_warnings: str

    @classmethod
    def of_cache(cls, meta: CacheMeta) -> Upstream:
        return cls(meta.upstream_key, meta.upstream_built_at, meta.contract_version, meta.contract_warnings)


def write_meta(con: duckdb.DuckDBPyConnection, meta: CacheMeta) -> None:
    recreate_tables(con, ["meta"])
    insert_rows(con, "meta", [meta.__dict__])


def read_meta_from(con: duckdb.DuckDBPyConnection) -> CacheMeta | None:
    row = con.execute(f"SELECT {', '.join(columns('meta'))} FROM meta LIMIT 1").fetchone()
    return CacheMeta(*row) if row else None


def read_meta(path: Path) -> CacheMeta | None:
    """The live cache's meta, or None when there is no usable cache (missing, corrupt, or without a meta row)."""
    if not path.exists():
        return None
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return None
    try:
        return read_meta_from(con)
    except duckdb.Error:
        return None
    finally:
        con.close()


def plan_refresh(
    meta: CacheMeta | None, upstream_key: str, contract_version: str, metrics_version: str, scoring_hash: str, force: bool
) -> RefreshPlan:
    if force:
        return RefreshPlan(True, True, "forced")
    if meta is None:
        return RefreshPlan(True, True, "no usable cache")
    if meta.upstream_key != upstream_key:
        return RefreshPlan(True, True, "upstream data changed")
    if meta.contract_version != contract_version:
        return RefreshPlan(True, True, "contract version changed")
    if meta.metrics_version != metrics_version:
        return RefreshPlan(True, True, "metrics code or history settings changed")
    if meta.scoring_hash != scoring_hash:
        return RefreshPlan(False, True, "scoring config changed")
    return RefreshPlan(False, False, "up to date")


def plan_offline(meta: CacheMeta | None, metrics_version: str, scoring_hash: str) -> RefreshPlan | None:
    """The plan when the API cannot be asked: only a scoring-config change can be served from the cache alone, else None."""
    if meta is None or meta.metrics_version != metrics_version or meta.scoring_hash == scoring_hash:
        return None
    return RefreshPlan(False, True, "scoring config changed")


def next_meta(
    plan: RefreshPlan,
    previous: CacheMeta | None,
    upstream_key: str,
    upstream_built_at: str | None,
    contract_version: str,
    contract_warnings: str,
    metrics_version: str,
    scoring_hash: str,
    now: str,
) -> CacheMeta:
    """After a score-only rebuild the data and contract are those of the previous cache; only the scoring hash moves."""
    if plan.stage1 or previous is None:
        return CacheMeta(upstream_key, upstream_built_at, contract_version, metrics_version, scoring_hash, now, contract_warnings)
    return CacheMeta(previous.upstream_key, previous.upstream_built_at, previous.contract_version, previous.metrics_version, scoring_hash, now, previous.contract_warnings)
