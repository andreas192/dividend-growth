"""Orchestration shared by the CLI and the PIPELINE tests. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
from starlette.applications import Starlette

from dgi.cache.build import compact_into, discard, new_path, prepare_work_file, swap_in
from dgi.cache.checks import CheckResult, run_cache_checks, verify_cache
from dgi.cache.handle import open_readonly
from dgi.cache.meta import CacheMeta, RefreshPlan, Upstream, next_meta, plan_offline, plan_refresh, read_meta, write_meta
from dgi.cache.status import CacheStatus, read_status
from dgi.client.contract import fetch_contract
from dgi.client.http import ApiClient
from dgi.client.pulls import build_pulls
from dgi.client.series import ApiPriceSource
from dgi.client.stage import stage_pull
from dgi.errors import ApiUnavailable, CacheCheckError
from dgi.fsutil import file_sha256
from dgi.metrics import MetricsResult, build_metrics, metrics_version
from dgi.results import RefreshResult
from dgi.scoring.config import ScoringConfig, load_scoring_config
from dgi.scoring.stage import ScoreResult, score_cache
from dgi.settings import Settings
from dgi.web.app import create_app


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
    upstream: Upstream,
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
        meta = next_meta(plan, previous, upstream.key, upstream.built_at, upstream.contract_version, upstream.contract_warnings, version, scoring_hash, now)
        write_meta(con, meta)
        candidate = new_path(live)
        compact_into(con, candidate)
        return candidate, rows, metrics, scores
    finally:
        con.close()
        discard(work)


def choose_plan(
    client: ApiClient, previous: CacheMeta | None, version: str, scoring_hash: str, force: bool
) -> tuple[Upstream, RefreshPlan, list[str]]:
    """Ask the API what changed. If it cannot be asked, a scoring-only edit is still rescored from the cache; anything else fails."""
    try:
        health = client.health()
        contract = fetch_contract(client)
    except ApiUnavailable as exc:
        plan = None if force else plan_offline(previous, version, scoring_hash)
        if plan is None or previous is None:
            raise
        notice = f"the API is unreachable, so upstream was not checked; rescored the cached data ({str(exc).splitlines()[0]})"
        return Upstream.of_cache(previous), plan, [notice]
    upstream = Upstream(health.upstream_key, health.gold_built_at, contract.version, "; ".join(contract.warnings))
    return upstream, plan_refresh(previous, upstream.key, upstream.contract_version, version, scoring_hash, force), contract.warnings


def run_refresh(settings: Settings, client: ApiClient, *, today: date, now: datetime, force: bool = False) -> RefreshResult:
    cfg = load_scoring_config(settings.scoring_path)
    live = settings.cache_path
    previous = read_meta(live)
    version = metrics_version(cfg.metrics)
    scoring_hash = file_sha256(settings.scoring_path)
    upstream, plan, warnings = choose_plan(client, previous, version, scoring_hash, force)
    if not (plan.stage1 or plan.score):
        return RefreshResult("up to date", plan.reason, upstream.key, {}, None, None, [], warnings)
    candidate, rows, metrics, scores = build_candidate(
        settings, client, cfg, plan, previous, upstream, today, now.isoformat(), version, scoring_hash
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
    return RefreshResult("rebuilt" if plan.stage1 else "rescored", plan.reason, upstream.key, rows, metrics, scores, checks, warnings)


def run_status(settings: Settings) -> CacheStatus:
    return read_status(settings.cache_path)


def run_check(settings: Settings) -> list[CheckResult]:
    with open_readonly(settings.cache_path) as con:
        return run_cache_checks(con, None)


def build_web_app(settings: Settings, client: ApiClient) -> Starlette:
    """The UI over the live cache; the company page's daily charts read one ticker at a time through `client`."""
    cfg = load_scoring_config(settings.scoring_path)
    return create_app(settings, cfg, ApiPriceSource(client), scoring_hash=file_sha256(settings.scoring_path))
