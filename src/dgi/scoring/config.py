from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from dgi.errors import ConfigError
from dgi.metrics.params import MetricParams

PILLARS = ("dividend", "safety", "growth", "valuation")
KNOWN_METRICS = frozenset({
    "streak", "no_cut_streak", "dgr_1", "dgr_3", "dgr_5", "dgr_10", "yield_vs_avg", "payment_frequency",
    "payout_earnings", "payout_earnings_5y", "payout_fcf", "payout_fcf_5y", "interest_coverage",
    "net_debt_ebitda", "current_ratio", "positive_earnings_years",
    "rev_cagr_5", "eps_cagr_5", "fcf_ps_cagr_5", "roe", "op_margin_std", "share_trend_5",
    "pe", "p_fcf", "fcf_yield", "margin_of_safety",
})


class StrictModel(BaseModel):
    """A config model: a key it does not know is a typo and an error."""

    model_config = ConfigDict(extra="forbid")


class Band(StrictModel):
    pillar: str
    weight: float = Field(gt=0)
    points: list[tuple[float, float]]

    @field_validator("pillar")
    @classmethod
    def known_pillar(cls, value: str) -> str:
        if value not in PILLARS:
            raise ValueError(f"unknown pillar {value!r}; use one of {', '.join(PILLARS)}")
        return value

    @field_validator("points")
    @classmethod
    def ordered(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(points) < 2:
            raise ValueError("a band needs at least two points")
        xs = [x for x, _ in points]
        if any(b <= a for a, b in zip(xs, xs[1:])):
            raise ValueError("band points must have strictly increasing x")
        if any(not 0 <= score <= 100 for _, score in points):
            raise ValueError("band scores must be between 0 and 100")
        return points


class Universe(StrictModel):
    min_dividend_years: int = 5
    max_fundamentals_age_days: int = 730
    min_coverage: float = Field(default=0.6, ge=0, le=1)
    excluded_sic_ranges: list[tuple[int, int]] = [(6000, 6199), (6300, 6411), (6798, 6798)]


class SectorRule(StrictModel):
    group: str
    sic: list[tuple[int, int]]


class HardFilters(StrictModel):
    min_streak: int = 5
    max_payout_fcf: float | None = 1.0
    max_payout_eps: float | None = None
    min_market_cap: float = 1e9
    min_yield: float = 0.0
    max_yield: float | None = None
    min_score: float = 0.0


class ValuationParams(StrictModel):
    required_return: float = Field(default=0.09, gt=0)
    growth_cap: float = Field(default=0.06, ge=0)
    growth_floor: float = Field(default=0.0, ge=0)
    range_delta: float = Field(default=0.01, ge=0)


class ScoringConfig(StrictModel):
    version: int = 1
    pillars: dict[str, float]
    metrics: MetricParams = MetricParams()
    universe: Universe = Universe()
    hard_filters: HardFilters = HardFilters()
    valuation: ValuationParams = ValuationParams()
    sector_groups: list[SectorRule] = []
    bands: dict[str, Band]

    @model_validator(mode="after")
    def consistent(self) -> "ScoringConfig":
        if set(self.pillars) != set(PILLARS) or any(w < 0 for w in self.pillars.values()) or sum(self.pillars.values()) <= 0:
            raise ValueError(f"pillars must give a non-negative weight, not all zero, to each of {', '.join(PILLARS)}")
        unknown = sorted(set(self.bands) - KNOWN_METRICS)
        if unknown:
            raise ValueError(f"unknown metric in bands: {', '.join(unknown)}")
        for pillar in PILLARS:
            if self.pillars[pillar] > 0 and not any(b.pillar == pillar for b in self.bands.values()):
                raise ValueError(f"pillar {pillar} has weight but no metric bands")
        return self


def parse_scoring_config(text: str) -> ScoringConfig:
    try:
        return ScoringConfig.model_validate(yaml.safe_load(text) or {})
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid scoring config: {exc}") from exc


def load_scoring_config(path: Path) -> ScoringConfig:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read scoring config {path}: {exc}") from exc
    return parse_scoring_config(text)
