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
