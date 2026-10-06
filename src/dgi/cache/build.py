"""Files of a cache build: working file, compacted candidate, atomic swap."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

import duckdb

from dgi.cache.meta import RefreshPlan
from dgi.fsutil import atomic_replace
from dgi.schema import PERSISTED_TABLES


def work_path(live: Path) -> Path:
    return live.with_name(live.name + ".work")


def new_path(live: Path) -> Path:
    return live.with_name(live.name + ".new")


def _remove(path: Path) -> None:
    for candidate in (path, path.with_name(path.name + ".wal")):
        candidate.unlink(missing_ok=True)


def prepare_work_file(live: Path, plan: RefreshPlan) -> Path:
    """Delete leftovers of a crashed run. A score-only rebuild starts from a copy of the live cache."""
    live.parent.mkdir(parents=True, exist_ok=True)
    work = work_path(live)
    _remove(work)
    _remove(new_path(live))
    if not plan.stage1:
        shutil.copyfile(live, work)
    return work


def compact_into(con: duckdb.DuckDBPyConnection, new: Path, tables: Iterable[str] = PERSISTED_TABLES) -> None:
    """Copy only the persisted tables into a fresh file, so staging space never reaches the live cache."""
    target = str(new).replace("'", "''")
    con.execute(f"ATTACH '{target}' AS compacted")
    try:
        for name in tables:
            con.execute(f"CREATE TABLE compacted.{name} AS SELECT * FROM main.{name}")
        con.execute("CHECKPOINT compacted")
    finally:
        con.execute("DETACH compacted")


def discard(path: Path) -> None:
    _remove(path)


def swap_in(new: Path, live: Path) -> None:
    """The new cache replaces the live one in a single rename; readers see the old file or the new one."""
    atomic_replace(new, live)
    live.with_name(live.name + ".wal").unlink(missing_ok=True)
