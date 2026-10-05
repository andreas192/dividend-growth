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
