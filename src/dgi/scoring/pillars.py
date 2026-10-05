from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dgi.scoring.bands import band_score
from dgi.scoring.config import PILLARS, ScoringConfig


@dataclass(frozen=True)
class MetricScore:
    metric: str
    pillar: str
    value: float
    band_score: float
    weight: float
    contribution: float


@dataclass(frozen=True)
class CompanyScore:
    pillars: dict[str, float | None]
    pillar_coverage: dict[str, float]
    coverage: float
    total: float | None
    details: list[MetricScore]


def score_company(values: Mapping[str, float | None], cfg: ScoringConfig) -> CompanyScore:
    pillar_scores: dict[str, float | None] = {}
    pillar_coverage: dict[str, float] = {}
    present: dict[str, list[tuple[str, float, float, float]]] = {p: [] for p in PILLARS}
    weight_all = {p: 0.0 for p in PILLARS}
    for metric, band in cfg.bands.items():
        weight_all[band.pillar] += band.weight
        value = values.get(metric)
        if value is not None:
            present[band.pillar].append((metric, value, band_score(band.points, value), band.weight))
    for pillar in PILLARS:
        weight_in = sum(w for *_, w in present[pillar])
        pillar_coverage[pillar] = weight_in / weight_all[pillar] if weight_all[pillar] else 0.0
        pillar_scores[pillar] = (
            sum(s * w for _, _, s, w in present[pillar]) / weight_in if weight_in else None
        )
    active = [p for p in PILLARS if pillar_scores[p] is not None and cfg.pillars[p] > 0]
    pillar_total = sum(cfg.pillars[p] for p in active)
    total = sum(cfg.pillars[p] * pillar_scores[p] for p in active) / pillar_total if active else None
    weight_sum = sum(cfg.pillars.values())
    coverage = sum(cfg.pillars[p] * pillar_coverage[p] for p in PILLARS) / weight_sum
    details = []
    for pillar in active:
        weight_in = sum(w for *_, w in present[pillar])
        share = cfg.pillars[pillar] / pillar_total
        for metric, value, score, weight in present[pillar]:
            details.append(MetricScore(metric, pillar, value, score, weight, share * score * weight / weight_in))
    return CompanyScore(pillar_scores, pillar_coverage, coverage, total, details)
