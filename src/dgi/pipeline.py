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
