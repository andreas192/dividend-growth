# Phase 5: Cache lifecycle and the refresh pipeline (Tasks 15-17)

Part of `../2026-10-05-dgi-screener.md` (read its Global Constraints, Review Focus and "Spec clarifications" first). Spec: `../../specs/2026-10-05-dgi-screener-design.md`, sections "Data flow and cache", "Safety" and "Errors".

How a refresh runs (this phase builds the pieces and wires them):

```
health + contract check -> plan_refresh(meta, keys) -> nothing to do? stop ("up to date")
  -> prepare_work_file  (full rebuild: empty file; score-only: copy of the live cache; leftovers removed)
  -> stage 1 (when upstream, contract, metrics code or `metrics:` config changed): pull_all -> build_metrics
  -> stage 2 (always when anything runs): score_cache -> write_meta
  -> compact_into  (only the persisted tables, into <live>.new)
  -> verify_cache  (quality checks; row counts compared with the live cache)
  -> failed? delete <live>.new, raise CacheCheckError (old cache keeps serving)
  -> swap_in       (one atomic rename)
```

Change keys (spec clarifications 1 and 6): `upstream_key` = `content_hash|gold_built_at`; `metrics_version` = metrics code version plus a hash of the `metrics:` section of `scoring.yaml`; `scoring_hash` = SHA-256 of the whole `scoring.yaml`. A scoring edit rescoring from the copied cache never calls the API for data.

DuckDB caches database instances per path inside one process: after a file is replaced, a new `connect()` to the same path in the same process keeps serving the old file until the old connection is closed. Every function here closes what it opens, and the web app (Task 18) closes before it reopens.

---

### Task 15: Cache meta, refresh planning, working and candidate files, atomic swap

> **Extended by Task 28 (`09-hardening.md`):** `meta.py` also has `Upstream` and `plan_offline`. Fixtures load `CFG` from the frozen config (Task 30).

**Files:**
- Create: `src/dgi/cache/__init__.py` (a docstring now; replaced in Tasks 18 and 19), `src/dgi/cache/meta.py`, `src/dgi/cache/build.py`
- Modify: `tests/cache_fixtures.py` (adds `make_meta`, `persist`)
- Test: `tests/test_cache_meta.py`, `tests/test_cache_build.py`

**Interfaces:**
- Consumes: `dgi.schema` (`columns`, `insert_rows`, `recreate_tables`, `PERSISTED_TABLES`), `dgi.fsutil.atomic_replace`.
- Produces:
  - `CacheMeta(upstream_key, upstream_built_at, contract_version, metrics_version, scoring_hash, built_at, contract_warnings="")` (frozen dataclass; `built_at` is an ISO 8601 UTC string because DuckDB `TIMESTAMP` drops the time zone; `contract_warnings` holds the deprecation notices seen at the last full rebuild, joined with `; `, for the UI footer), `RefreshPlan(stage1: bool, score: bool, reason: str)`.
  - `write_meta(con, meta)`, `read_meta_from(con) -> CacheMeta | None`, `read_meta(path) -> CacheMeta | None` (None for a missing, corrupt or meta-less file), `plan_refresh(meta, upstream_key, contract_version, metrics_version, scoring_hash, force) -> RefreshPlan`, `next_meta(plan, previous, upstream_key, upstream_built_at, contract_version, contract_warnings, metrics_version, scoring_hash, now) -> CacheMeta` (a score-only rebuild keeps the previous data identity, contract and warnings).
  - `work_path(live)`, `new_path(live)` (`<live>.work`, `<live>.new`), `prepare_work_file(live, plan) -> Path`, `compact_into(con, new, tables=PERSISTED_TABLES) -> None`, `discard(path)`, `swap_in(new, live)`.
  - Test helpers: `make_meta(**overrides) -> CacheMeta`, `persist(con, path, meta=None) -> Path`.

- [ ] **Step 1: Write the failing tests and the final fixtures**

`tests/cache_fixtures.py` (final version: adds make_meta and persist):
```python
"""Hand-built cache contents for the scoring, cache and web tests: no API, no network."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb

from dgi import schema
from dgi.cache.build import compact_into
from dgi.cache.meta import CacheMeta, write_meta
from dgi.scoring.config import ScoringConfig, load_scoring_config
from dgi.scoring.stage import score_cache

TODAY = dt.date(2026, 10, 5)
CFG: ScoringConfig = load_scoring_config(Path(__file__).resolve().parent.parent / "config" / "scoring.yaml")

# A healthy dividend grower's metrics_current row; tests override single values.
GOOD: dict[str, Any] = {
    "price": 60.0, "years_history": 12, "fy_period_end": dt.date(2025, 12, 31), "dividend_ttm": 1.8, "market_cap": 5e9,
    "div_yield": 0.03, "div_yield_avg_5y": 0.028, "yield_vs_avg": 0.05, "streak": 11, "no_cut_streak": 11,
    "dgr_1": 0.05, "dgr_3": 0.05, "dgr_5": 0.05, "dgr_10": 0.05, "payment_frequency": 4, "cut_years_5y": 0,
    "special_count_5y": 0, "suspect_dividend_count": 0, "irregular_payments": False, "latest_fy": 2025, "basis": "fy",
    "eps_basis": 3.3, "fcf_ps_basis": 3.0, "payout_earnings": 0.5, "payout_earnings_5y": 0.5, "payout_fcf": 0.6,
    "payout_fcf_5y": 0.6, "interest_coverage": 15.0, "net_debt_ebitda": 1.0, "current_ratio": 2.0,
    "positive_earnings_years": 10, "rev_cagr_5": 0.04, "eps_cagr_5": 0.06, "fcf_ps_cagr_5": 0.06, "roe": 0.2,
    "op_margin_std": 0.01, "share_trend_5": -0.01, "pe": 18.0, "p_fcf": 20.0, "fcf_yield": 0.05,
    "fcf_latest": 100.0, "net_income_latest": 160.0,
}


def cache_with(companies: dict[str, tuple[str, dict[str, Any]]]) -> duckdb.DuckDBPyConnection:
    """companies: ticker -> (sic, overrides of GOOD). Creates every cache table; fills company_dim and metrics_current."""
    con = duckdb.connect()
    schema.create_tables(con)
    schema.insert_rows(con, "company_dim", [{"ticker": t, "cik": i, "name": f"{t} Inc", "sic": sic} for i, (t, (sic, _)) in enumerate(companies.items())])
    schema.insert_rows(con, "metrics_current", [{"ticker": t, **{**GOOD, **over}} for t, (_, over) in companies.items()])
    return con


def scored_cache(companies: dict[str, tuple[str, dict[str, Any]]]) -> duckdb.DuckDBPyConnection:
    con = cache_with(companies)
    score_cache(con, CFG, TODAY)
    return con


def make_meta(**overrides: Any) -> CacheMeta:
    values: dict[str, Any] = dict(
        upstream_key="hash-1|2026-10-04T06:00:00+00:00", upstream_built_at="2026-10-04T06:00:00+00:00",
        contract_version="v1", metrics_version="1:abc", scoring_hash="s1", built_at="2026-10-05T07:00:00+00:00", contract_warnings="",
    )
    return CacheMeta(**{**values, **overrides})


def persist(con: duckdb.DuckDBPyConnection, path: Path, meta: CacheMeta | None = None) -> Path:
    """Write `con`'s cache tables (plus meta) to a cache file, as the pipeline's compaction does."""
    write_meta(con, meta or make_meta())
    compact_into(con, path)
    return path
```

`tests/test_cache_meta.py` (UNIT: write/read meta, plan_refresh, next_meta):
```python
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
```

`tests/test_cache_build.py` (UNIT: prepare_work_file, compact_into, swap_in, discard):
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cache_meta.py tests/test_cache_build.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.cache'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/cache/__init__.py`:
```python
"""The derived cache: meta and refresh planning, atomic build and swap, quality checks, read-only queries."""
```

`src/dgi/cache/meta.py`:
```python
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
```

`src/dgi/cache/build.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cache_meta.py tests/test_cache_build.py`
Expected: PASS (20 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/cache tests/cache_fixtures.py tests/test_cache_meta.py tests/test_cache_build.py
git commit -m "feat: cache meta, refresh planning, working file, compaction and atomic swap"
```

---

### Task 16: Cache quality checks and status

> **Changed by Task 27 (`09-hardening.md`):** `read_status` uses `open_readonly` and reports a schema mismatch as `CacheMissing`; only storage errors count as cache errors.

**Files:**
- Create: `src/dgi/cache/checks.py`, `src/dgi/cache/status.py`
- Test: `tests/test_cache_checks.py`, `tests/test_cache_status.py`

**Interfaces:**
- Consumes: `dgi.schema`, `dgi.cache.meta` (`CacheMeta`, `read_meta_from`), `CacheMissing`.
- Produces:
  - `CheckResult(name, passed, detail)`; `COUNTED_TABLES`; `row_counts(con) -> dict[str, int]`.
  - Checks, each returning a `CheckResult`: `check_schema(con)` (every persisted table has exactly the declared columns), `check_row_counts(counts, previous, min_ratio=0.8)` (companies must exist; no counted table may lose more than 20% against the previous cache), `check_no_duplicates(con)`, `check_streak_bounds(con)` (a streak never exceeds its history), `check_yield_outliers(con, max_yield=0.25, max_share=0.05)` (fails only when more than 5% of scored companies yield over 25%), `check_scored_share(con, min_share=0.02, max_share=0.90)`.
  - `run_cache_checks(con, previous_counts=None) -> list[CheckResult]` (stops after a failed schema check, because the others would only fail confusingly), `live_counts(live: Path) -> dict[str, int] | None`, `verify_cache(candidate: Path, live: Path) -> list[CheckResult]`.
  - `CacheStatus(meta, counts, scored, not_scored: dict[str, int])`, `read_status_from(con)`, `read_status(path) -> CacheStatus` (raises `CacheMissing` with a way out for a missing, corrupt or meta-less cache).

- [ ] **Step 1: Write the failing tests**

`tests/test_cache_checks.py` (UNIT: every check and verify_cache):
```python
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
```

`tests/test_cache_status.py` (UNIT: read_status):
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cache_checks.py tests/test_cache_status.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.cache.checks'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/cache/checks.py`:
```python
"""Quality checks on a cache file. A new cache must pass these before it replaces the live one."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import duckdb

from dgi import schema

COUNTED_TABLES = ("company_dim", "metrics_current", "dividend_annual", "fundamentals_annual", "scores")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {name: con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in COUNTED_TABLES}


def check_schema(con: duckdb.DuckDBPyConnection) -> CheckResult:
    problems = [
        name for name in schema.PERSISTED_TABLES if schema.table_columns(con, name) != schema.columns(name)
    ]
    if problems:
        return CheckResult("schema", False, "missing or different columns in: " + ", ".join(problems))
    return CheckResult("schema", True, f"{len(schema.PERSISTED_TABLES)} tables as declared")


def check_row_counts(counts: Mapping[str, int], previous: Mapping[str, int] | None, min_ratio: float = 0.8) -> CheckResult:
    if counts["company_dim"] == 0:
        return CheckResult("row_counts", False, "the cache has no companies")
    if previous is None:
        return CheckResult("row_counts", True, "no previous cache to compare with")
    collapsed = [
        f"{name} {previous[name]} -> {counts[name]}"
        for name in COUNTED_TABLES
        if previous.get(name, 0) > 0 and counts[name] < previous[name] * min_ratio
    ]
    if collapsed:
        return CheckResult("row_counts", False, "rows collapsed against the previous cache: " + "; ".join(collapsed))
    return CheckResult("row_counts", True, "no table lost more than " + f"{1 - min_ratio:.0%} of its rows")


def check_no_duplicates(con: duckdb.DuckDBPyConnection) -> CheckResult:
    keys = {
        "company_dim": "ticker", "metrics_current": "ticker", "scores": "ticker",
        "dividend_annual": "ticker, year", "fundamentals_annual": "ticker, fiscal_year",
    }
    duplicated = [
        table for table, key in keys.items()
        if con.execute(f"SELECT count(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING count(*) > 1)").fetchone()[0] > 0
    ]
    if duplicated:
        return CheckResult("no_duplicates", False, "duplicate keys in: " + ", ".join(duplicated))
    return CheckResult("no_duplicates", True, "keys are unique")


def check_streak_bounds(con: duckdb.DuckDBPyConnection) -> CheckResult:
    bad = con.execute(
        "SELECT count(*) FROM metrics_current WHERE streak > COALESCE(years_history, 0) - 1 OR no_cut_streak > COALESCE(years_history, 0) - 1"
    ).fetchone()[0]
    if bad:
        return CheckResult("streak_bounds", False, f"{bad} companies have a streak longer than their history")
    return CheckResult("streak_bounds", True, "no streak exceeds its history")


def check_yield_outliers(con: duckdb.DuckDBPyConnection, max_yield: float = 0.25, max_share: float = 0.05) -> CheckResult:
    scored, outliers = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE m.div_yield > ?) FROM scores s JOIN metrics_current m USING (ticker) WHERE s.status = 'scored'",
        [max_yield],
    ).fetchone()
    detail = f"{outliers} of {scored} scored companies yield more than {max_yield:.0%}"
    if scored and outliers / scored > max_share:
        return CheckResult("yield_outliers", False, detail + f" (over {max_share:.0%}: the price or dividend data looks wrong)")
    return CheckResult("yield_outliers", True, detail)


def check_scored_share(con: duckdb.DuckDBPyConnection, min_share: float = 0.02, max_share: float = 0.90) -> CheckResult:
    total, scored = con.execute("SELECT count(*), count(*) FILTER (WHERE status = 'scored') FROM scores").fetchone()
    if total == 0:
        return CheckResult("scored_share", False, "no companies were scored or listed")
    share = scored / total
    detail = f"{scored} of {total} companies scored ({share:.1%})"
    if not min_share <= share <= max_share:
        return CheckResult("scored_share", False, detail + f"; expected between {min_share:.0%} and {max_share:.0%}")
    return CheckResult("scored_share", True, detail)


def run_cache_checks(con: duckdb.DuckDBPyConnection, previous_counts: Mapping[str, int] | None = None) -> list[CheckResult]:
    shape = check_schema(con)
    if not shape.passed:
        return [shape]
    return [
        shape,
        check_row_counts(row_counts(con), previous_counts),
        check_no_duplicates(con),
        check_streak_bounds(con),
        check_yield_outliers(con),
        check_scored_share(con),
    ]


def live_counts(live: Path) -> dict[str, int] | None:
    """Row counts of the live cache, or None when there is none to compare with (missing or unreadable)."""
    if not live.exists():
        return None
    try:
        con = duckdb.connect(str(live), read_only=True)
    except duckdb.Error:
        return None
    try:
        return row_counts(con)
    except duckdb.Error:
        return None
    finally:
        con.close()


def verify_cache(candidate: Path, live: Path) -> list[CheckResult]:
    """Check `candidate`, comparing its row counts with the live cache when there is one."""
    con = duckdb.connect(str(candidate), read_only=True)
    try:
        return run_cache_checks(con, live_counts(live))
    finally:
        con.close()
```

`src/dgi/cache/status.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cache_checks.py tests/test_cache_status.py`
Expected: PASS (15 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/cache/checks.py src/dgi/cache/status.py tests/test_cache_checks.py tests/test_cache_status.py
git commit -m "feat: cache quality checks and status"
```

---

### Task 17: Refresh pipeline, `refresh` / `status` / `check` commands, and the PIPELINE tests

> **Changed by Tasks 27, 28 and 30 (`09-hardening.md`):** `run_refresh` asks `choose_plan` (offline rescore when the API is down and only the scoring config changed); `build_candidate` takes an `Upstream`; `run_check` uses `open_readonly`; `guard` catches `STORAGE_ERRORS` (and `ValidationError`); the pipeline tests copy `tests/frozen/scoring.yaml`.

Wiring only: `pipeline.py` and `cli.py` have no unit spec. Their logic lives in the functions of Tasks 4-16; this task adds the output formatters (unit-tested), the fake dataset, and the PIPELINE tests that prove the commands, stages and cache are wired.

**Files:**
- Create: `src/dgi/results.py`, `src/dgi/pipeline.py`, `tests/sample_gold.py`, `tests/test_cli_pipeline.py`, `tests/test_architecture.py`
- Modify: `src/dgi/report.py` (replace), `src/dgi/cli.py` (replace), `tests/test_report.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2-16.
- Produces:
  - `results.RefreshResult(action, reason, upstream_key, rows_pulled, metrics, scores, checks, warnings)` (its own module, so `report.py` and the unit tests can import it without importing the wiring in `pipeline.py`); `pipeline.pull_all(con, client, today) -> dict[str, int]`; `build_candidate(...)`; `run_refresh(settings, client, *, today, now, force=False) -> RefreshResult`; `run_status(settings) -> CacheStatus`; `run_check(settings) -> list[CheckResult]` (raises `CacheMissing` when there is no cache).
  - `report.py`: `guard`, `describe_refresh(result) -> str`, `describe_status(status, path) -> str`, `print_checks(results) -> None`.
  - CLI: `dgi refresh [--force] [--api URL]`, `dgi status`, `dgi check`; module-level `make_client(settings)`, `current_date()`, `current_time()` are the seams the PIPELINE tests replace.
  - Test data: `tests/sample_gold.py: build_sample_gold() -> dict[str, pa.Table]` (companies ACME, BANKY, NEWCO, NOPAY).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report.py`:

`_aux/test_report_task17.py` (UNIT: describe_refresh, describe_status, print_checks):
```python
def test_describe_refresh_summarizes_a_rebuild_and_a_no_op():
    from dgi.cache.checks import CheckResult
    from dgi.metrics import MetricsResult
    from dgi.report import describe_refresh
    from dgi.results import RefreshResult
    from dgi.scoring.stage import ScoreResult

    built = RefreshResult(
        "rebuilt", "upstream data changed", "h|t", {"stg_company": 5}, MetricsResult(5, 100, 3), ScoreResult(5, 2, {"no_price": 1, "financial_or_reit": 2}),
        [CheckResult("a", True, "x"), CheckResult("b", True, "y")], ["contract v1 is deprecated, sunset 2027-01-01"],
    )
    text = describe_refresh(built)
    assert "rebuilt: upstream data changed" in text and "pulled: stg_company=5" in text
    assert "metrics: 5 companies, 100 dividend payments, 3 with a streak" in text
    assert "scores: 2 scored of 5; not scored: financial_or_reit=2, no_price=1" in text
    assert "checks: 2 of 2 passed" in text and "warning: contract v1 is deprecated" in text
    noop = RefreshResult("up to date", "up to date", "h|t", {}, None, None, [], [])
    assert describe_refresh(noop) == "up to date: up to date\nupstream: h|t"


def test_describe_status_and_print_checks(capsys, tmp_path):
    from dgi.cache.checks import CheckResult
    from dgi.cache.status import CacheStatus
    from dgi.report import describe_status, print_checks
    from tests.cache_fixtures import make_meta

    status = CacheStatus(make_meta(), {"company_dim": 4}, 1, {"no_price": 3})
    text = describe_status(status, tmp_path / "dgi.duckdb")
    assert "companies: 4, scored: 1" in text and "not scored: no_price=3" in text and "contract: v1, metrics 1:abc, scoring s1" in text
    print_checks([CheckResult("schema", True, "ok"), CheckResult("row_counts", False, "collapsed")])
    out = capsys.readouterr().out
    assert "PASS schema: ok" in out and "FAIL row_counts: collapsed" in out
```

`tests/test_architecture.py` (UNIT: the one-way dependency rule):
```python
"""The one-way dependency rule from CLAUDE.md, enforced: client -> metrics -> scoring -> cache -> web."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "dgi"
LEAVES = {"errors", "settings", "fsutil", "schema"}
ALLOWED = {
    "client": LEAVES | {"client"},
    "metrics": LEAVES | {"metrics"},
    "scoring": LEAVES | {"metrics", "scoring"},
    "cache": LEAVES | {"scoring", "cache"},
    "web": {"errors", "settings", "cache", "web"},
}


def dgi_imports(path: Path) -> set[str]:
    """The first component after `dgi` of every absolute import in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[1] for a in node.names if a.name.startswith("dgi.")}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            parts = node.module.split(".")
            if parts[0] == "dgi" and len(parts) > 1:
                found.add(parts[1])
            elif parts[0] == "dgi":
                found |= {a.name for a in node.names}
    return found


def test_the_import_scanner_reads_each_import_form(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import dgi.client.http\nfrom dgi.cache.meta import x\nfrom dgi import schema, fsutil\nimport os\nfrom os import path\n")
    assert dgi_imports(f) == {"client", "cache", "schema", "fsutil"}


@pytest.mark.parametrize("package", sorted(ALLOWED))
def test_each_layer_imports_only_what_it_may(package):
    folder = SRC / package
    if not folder.exists():
        pytest.skip(f"{package} is added by a later task")
    offenders = {
        str(path.relative_to(SRC)): sorted(dgi_imports(path) - ALLOWED[package])
        for path in folder.rglob("*.py")
        if dgi_imports(path) - ALLOWED[package]
    }
    assert offenders == {}


def test_only_the_pipeline_and_the_cli_import_the_client():
    users = {p.name for p in SRC.glob("*.py") if "client" in dgi_imports(p)}
    assert users <= {"pipeline.py", "cli.py"}
```

`tests/sample_gold.py` (a tiny investment-API dataset built in code):
```python
"""A tiny investment-API dataset built in code: one dividend grower, a bank with the same numbers,
a company with a short dividend history, and one that pays nothing. Values are chosen so the
traced results are easy to state: ACME has raised its dividend 11 years in a row (2015-2025)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pyarrow as pa

D = dt.date
GROWER_YEARS = range(2014, 2026)
QUARTER_ENDS = [D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30)]  # TTM ends after the 2025 fiscal year

COMPANIES = [
    (1001, "Acme Beverages", "ACME", "2080", "Beverages"),
    (1002, "Banky Financial", "BANKY", "6021", "National commercial banks"),
    (1003, "Newco Industries", "NEWCO", "3000", "Misc manufacturing"),
    (1004, "Nopay Software", "NOPAY", "7370", "Computer services"),
]

STATEMENT_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("cik", pa.int64()), ("concept", pa.string()), ("period_kind", pa.string()),
    ("period_start", pa.date32()), ("period_end", pa.date32()), ("fiscal_year", pa.int32()), ("fiscal_period", pa.string()),
    ("value", pa.float64()), ("unit", pa.string()), ("filed", pa.date32()), ("source_accession", pa.string()), ("data_flag", pa.string()),
])


def _statement_row(ticker: str, cik: int, concept: str, kind: str, end: dt.date, fy: int, period: str, value: float, lag: int) -> dict[str, Any]:
    start = None if kind == "instant" else end - dt.timedelta(days=364 if kind == "annual" else 89)
    return {"ticker": ticker, "cik": cik, "concept": concept, "period_kind": kind, "period_start": start, "period_end": end,
            "fiscal_year": fy, "fiscal_period": period, "value": value, "unit": "USD", "filed": end + dt.timedelta(days=lag),
            "source_accession": f"{cik}-{fy}", "data_flag": None}


def grower_statements(ticker: str, cik: int) -> dict[str, list[dict[str, Any]]]:
    income, cash, balance = [], [], []
    for fy in GROWER_YEARS:
        n, end = fy - 2014, D(fy, 12, 31)
        shares = 100e6 * 0.99 ** n
        for concept, value in (("revenue", 1000e6 * 1.04 ** n), ("net_income", 100e6 * 1.05 ** n), ("operating_income", 150e6 * 1.05 ** n),
                               ("interest_expense", 10e6), ("shares_diluted_weighted", shares), ("eps_diluted", 100e6 * 1.05 ** n / shares)):
            income.append(_statement_row(ticker, cik, concept, "annual", end, fy, "FY", value, 60))
        for concept, value in (("cash_from_operations", 120e6 * 1.05 ** n), ("capex", -20e6), ("dividends_paid", -50e6 * 1.05 ** n), ("depreciation_amortization", 30e6)):
            cash.append(_statement_row(ticker, cik, concept, "annual", end, fy, "FY", value, 60))
        for concept, value in (("total_equity", 500e6), ("current_assets", 300e6), ("current_liabilities", 150e6), ("cash_and_equivalents", 50e6), ("long_term_debt", 200e6)):
            balance.append(_statement_row(ticker, cik, concept, "instant", end, fy, "FY", value, 60))
    for end in QUARTER_ENDS:
        for concept, value in (("net_income", 40e6), ("shares_diluted_weighted", 88e6)):
            income.append(_statement_row(ticker, cik, concept, "quarter", end, end.year, "Q", value, 40))
        for concept, value in (("cash_from_operations", 35e6), ("capex", -6e6)):
            cash.append(_statement_row(ticker, cik, concept, "quarter", end, end.year, "Q", value, 40))
    balance.append(_statement_row(ticker, cik, "shares_outstanding_cover", "instant", QUARTER_ENDS[-1], 2026, "Q", 88e6, 40))
    return {"income": income, "cash": cash, "balance": balance}


def quarterly_dividends(ticker: str, cik: int, years: range, base: float, growth: float, last_year_months: tuple[int, ...] = (2, 5, 8, 11)):
    rows = []
    for year in years:
        for month in last_year_months:
            rows.append((ticker, cik, D(year, month, 10), base * (1 + growth) ** (year - years.start)))
    return rows


def build_sample_gold() -> dict[str, pa.Table]:
    income: list = []
    cash: list = []
    balance: list = []
    dividends: list = []
    prices: list = []
    for cik, ticker in ((1001, "ACME"), (1002, "BANKY")):
        facts = grower_statements(ticker, cik)
        income += facts["income"]; cash += facts["cash"]; balance += facts["balance"]
        dividends += quarterly_dividends(ticker, cik, GROWER_YEARS, 0.25, 0.05)
        dividends += [(ticker, cik, D(2026, m, 10), 0.25 * 1.05 ** 12) for m in (2, 5, 8)]
    dividends += quarterly_dividends("NEWCO", 1003, range(2024, 2026), 0.10, 0.0)
    for cik, _, ticker, _, _ in COMPANIES:
        for year in GROWER_YEARS:
            prices.append((ticker, cik, D(year, 12, 30), 20.0 * 1.1 ** (year - 2014)))
        prices += [(ticker, cik, D(2026, 10, 1), 59.0), (ticker, cik, D(2026, 10, 2), 60.0)]

    def statement(rows):
        return pa.Table.from_pylist(rows, schema=STATEMENT_SCHEMA)

    return {
        "company": pa.table({
            "cik": pa.array([c[0] for c in COMPANIES], pa.int64()), "name": [c[1] for c in COMPANIES], "ticker": [c[2] for c in COMPANIES],
            "sic": [c[3] for c in COMPANIES], "sic_description": [c[4] for c in COMPANIES]}),
        "dividend_events": pa.table({
            "ticker": [r[0] for r in dividends], "cik": pa.array([r[1] for r in dividends], pa.int64()),
            "ex_date": pa.array([r[2] for r in dividends], pa.date32()), "amount": [r[3] for r in dividends]}),
        "split_events": pa.table({
            "ticker": pa.array([], pa.string()), "cik": pa.array([], pa.int64()), "ex_date": pa.array([], pa.date32()),
            "numerator": pa.array([], pa.float64()), "denominator": pa.array([], pa.float64()), "ratio": pa.array([], pa.float64())}),
        "price_daily": pa.table({
            "ticker": [r[0] for r in prices], "cik": pa.array([r[1] for r in prices], pa.int64()),
            "trade_date": pa.array([r[2] for r in prices], pa.date32()), "open": [r[3] for r in prices], "high": [r[3] for r in prices],
            "low": [r[3] for r in prices], "close": [r[3] for r in prices], "volume": pa.array([1_000_000] * len(prices), pa.int64())}),
        "price_daily_adjusted": pa.table({
            "ticker": [r[0] for r in prices], "cik": pa.array([r[1] for r in prices], pa.int64()),
            "trade_date": pa.array([r[2] for r in prices], pa.date32()), "close": [r[3] for r in prices],
            "split_adjusted_close": [r[3] for r in prices], "total_return_close": [r[3] for r in prices]}),
        "income_statement_latest": statement(income),
        "cash_flow_latest": statement(cash),
        "balance_sheet_latest": statement(balance),
    }
```

`tests/test_cli_pipeline.py` (PIPELINE: commands, stages and cache wired over the fake API):
```python
"""PIPELINE layer: the commands, the stages and the cache wired together over a fake investment API."""

import datetime as dt
import hashlib
import shutil
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from dgi import cli
from dgi.cache.meta import read_meta
from tests.fake_api import FakeGold
from tests.sample_gold import build_sample_gold

runner = CliRunner()
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    scoring = tmp_path / "scoring.yaml"
    shutil.copy(REPO / "config" / "scoring.yaml", scoring)
    monkeypatch.setattr(cli, "current_date", lambda: dt.date(2026, 10, 5))
    monkeypatch.setattr(cli, "current_time", lambda: dt.datetime(2026, 10, 5, 7, 0, tzinfo=dt.timezone.utc))
    return {"DGI_DATA_DIR": str(tmp_path / "data"), "DGI_SCORING_CONFIG": str(scoring), "DGI_INVEST_API_URL": "http://api.test"}


@pytest.fixture
def gold(monkeypatch):
    fake = FakeGold(build_sample_gold())
    monkeypatch.setattr(cli, "make_client", lambda settings: fake.client(page_limit=500))
    return fake


def cache(env):
    return duckdb.connect(str(Path(env["DGI_DATA_DIR"]) / "dgi.duckdb"), read_only=True)


def sha(env) -> str:
    return hashlib.sha256((Path(env["DGI_DATA_DIR"]) / "dgi.duckdb").read_bytes()).hexdigest()


def test_the_commands_are_registered():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("refresh", "status", "check"):
        assert command in result.output


def test_refresh_builds_a_cache_and_one_value_traces_from_the_api_to_the_score(env, gold):
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert result.exit_code == 0, result.output
    assert "rebuilt: no usable cache" in result.output and "checks: 6 of 6 passed" in result.output
    con = cache(env)
    streak, status, price, basis = con.execute(
        "SELECT m.streak, s.status, m.price, m.basis FROM metrics_current m JOIN scores s USING (ticker) WHERE ticker = 'ACME'"
    ).fetchone()
    assert (streak, status, price, basis) == (11, "scored", 60.0, "ttm")
    assert con.execute("SELECT ticker, status, reason FROM scores ORDER BY ticker").fetchall() == [
        ("ACME", "scored", None),
        ("BANKY", "not_scored", "financial_or_reit"),
        ("NEWCO", "not_scored", "short_dividend_history"),
        ("NOPAY", "not_scored", "short_dividend_history"),
    ]
    assert con.execute("SELECT upstream_key FROM meta").fetchone() == ("hash-1|2026-10-04T06:00:00+00:00",)
    assert not list(Path(env["DGI_DATA_DIR"]).glob("dgi.duckdb.*"))


def test_a_second_refresh_on_unchanged_upstream_does_nothing(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    before, requests = sha(env), len(gold.requests)
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert result.exit_code == 0 and "up to date" in result.output
    assert sha(env) == before
    assert all("/v1/" not in r for r in gold.requests[requests:])  # only /health and /contracts were asked


def test_new_upstream_data_rebuilds_and_a_scoring_edit_rescores_without_the_api(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    gold.built_at = "2026-10-05T06:00:00+00:00"
    assert "rebuilt: upstream data changed" in runner.invoke(cli.app, ["refresh"], env=env).output
    scoring = Path(env["DGI_SCORING_CONFIG"])
    scoring.write_text(scoring.read_text().replace("min_streak: 5", "min_streak: 7"))
    requests = len(gold.requests)
    result = runner.invoke(cli.app, ["refresh"], env=env)
    assert "rescored: scoring config changed" in result.output
    assert all("/v1/" not in r for r in gold.requests[requests:])
    assert read_meta(Path(env["DGI_DATA_DIR"]) / "dgi.duckdb").upstream_key == "hash-1|2026-10-05T06:00:00+00:00"


def test_refresh_force_rebuilds_even_when_nothing_changed(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    assert "rebuilt: forced" in runner.invoke(cli.app, ["refresh", "--force"], env=env).output


def test_refresh_with_the_api_down_exits_1_and_keeps_the_old_cache(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    before = sha(env)
    gold.down = True
    result = runner.invoke(cli.app, ["refresh", "--force"], env=env)
    assert result.exit_code == 1 and "error:" in result.output
    assert sha(env) == before


def test_status_and_check_read_the_live_cache(env, gold):
    runner.invoke(cli.app, ["refresh"], env=env)
    status = runner.invoke(cli.app, ["status"], env=env)
    assert status.exit_code == 0 and "companies: 4, scored: 1" in status.output and "financial_or_reit=1" in status.output
    check = runner.invoke(cli.app, ["check"], env=env)
    assert check.exit_code == 0 and "FAIL" not in check.output
    # a 4-company sample scores 25%: inside the plausible range of the scored-share check


def test_check_without_a_cache_exits_1_with_a_way_out(env):
    result = runner.invoke(cli.app, ["check"], env=env)
    assert result.exit_code == 1 and "run `dgi refresh`" in result.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_report.py tests/test_cli_pipeline.py`
Expected: FAIL (`ImportError: cannot import name 'describe_refresh' from 'dgi.report'`, `ImportError` for `dgi.pipeline`).

- [ ] **Step 3: Write the implementation**

`src/dgi/results.py`:
```python
"""Result types shared by the pipeline and the output formatting."""

from __future__ import annotations

from dataclasses import dataclass

from dgi.cache.checks import CheckResult
from dgi.metrics import MetricsResult
from dgi.scoring.stage import ScoreResult


@dataclass(frozen=True)
class RefreshResult:
    action: str  # "up to date" | "rescored" | "rebuilt"
    reason: str
    upstream_key: str
    rows_pulled: dict[str, int]
    metrics: MetricsResult | None
    scores: ScoreResult | None
    checks: list[CheckResult]
    warnings: list[str]
```

`src/dgi/pipeline.py` (Task 22 adds build_web_app):
```python
"""Orchestration shared by the CLI and the PIPELINE tests. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb

from dgi.cache.build import compact_into, discard, new_path, prepare_work_file, swap_in
from dgi.cache.checks import CheckResult, run_cache_checks, verify_cache
from dgi.cache.meta import CacheMeta, RefreshPlan, next_meta, plan_refresh, read_meta, write_meta
from dgi.cache.status import CacheStatus, read_status
from dgi.client.contract import ContractStatus, fetch_contract
from dgi.client.http import ApiClient, Health
from dgi.client.pulls import build_pulls
from dgi.client.stage import stage_pull
from dgi.errors import CacheCheckError, CacheMissing
from dgi.fsutil import file_sha256
from dgi.metrics import MetricsResult, build_metrics, metrics_version
from dgi.results import RefreshResult
from dgi.scoring.config import ScoringConfig, load_scoring_config
from dgi.scoring.stage import ScoreResult, score_cache
from dgi.settings import Settings


def pull_all(con: duckdb.DuckDBPyConnection, client: ApiClient, today: date) -> dict[str, int]:
    rows: dict[str, int] = {}
    for pull in build_pulls(today):
        rows[pull.table] = rows.get(pull.table, 0) + stage_pull(con, client.pages(pull.resource, pull.filters), pull.table)
    return rows


def build_candidate(
    settings: Settings,
    client: ApiClient,
    cfg: ScoringConfig,
    plan: RefreshPlan,
    previous: CacheMeta | None,
    health: Health,
    contract: ContractStatus,
    today: date,
    now: str,
    version: str,
    scoring_hash: str,
) -> tuple[Path, dict[str, int], MetricsResult | None, ScoreResult]:
    """Write the next cache next to the live one as `<live>.new`. The working file is always removed."""
    live = settings.cache_path
    work = prepare_work_file(live, plan)
    con = duckdb.connect(str(work))
    try:
        rows: dict[str, int] = {}
        metrics = None
        if plan.stage1:
            rows = pull_all(con, client, today)
            metrics = build_metrics(con, today, cfg.metrics)
        scores = score_cache(con, cfg, today)
        write_meta(con, next_meta(plan, previous, health.upstream_key, health.gold_built_at, contract.version, "; ".join(contract.warnings), version, scoring_hash, now))
        candidate = new_path(live)
        compact_into(con, candidate)
        return candidate, rows, metrics, scores
    finally:
        con.close()
        discard(work)


def run_refresh(settings: Settings, client: ApiClient, *, today: date, now: datetime, force: bool = False) -> RefreshResult:
    cfg = load_scoring_config(settings.scoring_path)
    health = client.health()
    contract = fetch_contract(client)
    live = settings.cache_path
    previous = read_meta(live)
    version = metrics_version(cfg.metrics)
    scoring_hash = file_sha256(settings.scoring_path)
    plan = plan_refresh(previous, health.upstream_key, contract.version, version, scoring_hash, force)
    if not (plan.stage1 or plan.score):
        return RefreshResult("up to date", plan.reason, health.upstream_key, {}, None, None, [], contract.warnings)
    candidate, rows, metrics, scores = build_candidate(
        settings, client, cfg, plan, previous, health, contract, today, now.isoformat(), version, scoring_hash
    )
    checks = verify_cache(candidate, live)
    failed = [c for c in checks if not c.passed]
    if failed:
        discard(candidate)
        raise CacheCheckError(
            "the new cache failed its checks and was not used; the previous cache keeps serving: "
            + "; ".join(f"{c.name}: {c.detail}" for c in failed)
        )
    swap_in(candidate, live)
    return RefreshResult("rebuilt" if plan.stage1 else "rescored", plan.reason, health.upstream_key, rows, metrics, scores, checks, contract.warnings)


def run_status(settings: Settings) -> CacheStatus:
    return read_status(settings.cache_path)


def run_check(settings: Settings) -> list[CheckResult]:
    path = settings.cache_path
    if not path.exists():
        raise CacheMissing(f"no cache at {path}; run `dgi refresh`")
    con = duckdb.connect(str(path), read_only=True)
    try:
        return run_cache_checks(con, None)
    finally:
        con.close()
```

`src/dgi/report.py` (Task 22 adds exposure_warning):
```python
"""CLI output formatting and the error-to-exit-code guard. No business logic."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

import typer

from dgi.cache.checks import CheckResult
from dgi.cache.status import CacheStatus
from dgi.errors import DgiError
from dgi.results import RefreshResult

T = TypeVar("T")


def guard(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except DgiError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _reasons(counts: dict[str, int]) -> str:
    return ", ".join(f"{reason}={n}" for reason, n in sorted(counts.items())) or "none"


def describe_refresh(result: RefreshResult) -> str:
    lines = [f"{result.action}: {result.reason}", f"upstream: {result.upstream_key}"]
    if result.rows_pulled:
        lines.append("pulled: " + ", ".join(f"{t}={n}" for t, n in result.rows_pulled.items()))
    if result.metrics:
        m = result.metrics
        lines.append(f"metrics: {m.companies} companies, {m.dividend_payments} dividend payments, {m.with_streak} with a streak")
    if result.scores:
        s = result.scores
        lines.append(f"scores: {s.scored} scored of {s.companies}; not scored: {_reasons(s.not_scored)}")
    if result.checks:
        lines.append(f"checks: {sum(c.passed for c in result.checks)} of {len(result.checks)} passed")
    lines += [f"warning: {w}" for w in result.warnings]
    return "\n".join(lines)


def describe_status(status: CacheStatus, path: Path) -> str:
    m = status.meta
    return "\n".join([
        f"cache: {path}",
        f"built: {m.built_at}",
        f"upstream: {m.upstream_key}",
        f"contract: {m.contract_version}, metrics {m.metrics_version}, scoring {m.scoring_hash[:12]}",
        f"companies: {status.counts['company_dim']}, scored: {status.scored}",
        f"not scored: {_reasons(status.not_scored)}",
    ])


def print_checks(results: Iterable[CheckResult]) -> None:
    for r in results:
        typer.echo(f"{'PASS' if r.passed else 'FAIL'} {r.name}: {r.detail}")
```

`src/dgi/cli.py` (Task 22 adds serve):
```python
"""Command line entry point. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import typer

from dgi.client.http import ApiClient
from dgi.pipeline import run_check, run_refresh, run_status
from dgi.report import describe_refresh, describe_status, guard, print_checks
from dgi.settings import Settings

app = typer.Typer(help="Dividend growth screener over the investment platform API.", no_args_is_help=True)


def make_client(settings: Settings) -> ApiClient:
    return ApiClient(settings.api_url, timeout=settings.http_timeout, page_limit=settings.page_limit)


def current_date() -> date:
    return date.today()


def current_time() -> datetime:
    return datetime.now(timezone.utc)


@app.command()
def refresh(
    force: bool = typer.Option(False, "--force", help="Ignore the change keys and rebuild everything."),
    api: str | None = typer.Option(None, "--api", help="Investment API base URL (default: DGI_INVEST_API_URL)."),
) -> None:
    """Pull from the API when upstream changed, build metrics, score, check, and swap the cache in."""
    settings = Settings.from_env()
    if api:
        settings = settings.model_copy(update={"api_url": api})
    with make_client(settings) as client:
        result = guard(lambda: run_refresh(settings, client, today=current_date(), now=current_time(), force=force))
    typer.echo(describe_refresh(result))


@app.command()
def status() -> None:
    """Show what the cache was built from and what it holds."""
    settings = Settings.from_env()
    typer.echo(describe_status(guard(lambda: run_status(settings)), settings.cache_path))


@app.command()
def check() -> None:
    """Run the quality checks on the live cache."""
    settings = Settings.from_env()
    results = guard(lambda: run_check(settings))
    print_checks(results)
    if not all(r.passed for r in results):
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run to verify it passes, then the whole suite**

Run: `uv run pytest tests/test_report.py tests/test_cli_pipeline.py tests/test_architecture.py`
Expected: PASS (6 + 8 passed; the architecture test passes with one skip until the web package exists).

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Step 5: Smoke the CLI help**

Run: `uv run dgi --help`
Expected: lists `refresh`, `status` and `check` (`serve` arrives in Task 22).

- [ ] **Step 6: Commit**

```bash
git add src/dgi/results.py src/dgi/pipeline.py src/dgi/report.py src/dgi/cli.py tests/sample_gold.py tests/test_cli_pipeline.py tests/test_architecture.py tests/test_report.py
git commit -m "feat: refresh pipeline with refresh, status and check commands"
```
