# Phase 4: Scoring (Tasks 11-14)

Part of `../2026-10-05-dgi-screener.md` (read its Global Constraints, Review Focus and "Spec clarifications" first). Spec: `../../specs/2026-10-05-dgi-screener-design.md`, section "Scoring rules".

The score stage reads only the cache (`metrics_current`, `company_dim`) and `config/scoring.yaml`, and writes `scores`, `score_detail` and `flags`. Editing `scoring.yaml` therefore never needs an API pull (except the `metrics:` section, which `metrics/` reads in stage 1).

Rules this phase implements:

- Each metric maps to 0-100 by **linear interpolation** between `(x, score)` points from the config, clamped at both ends. Absolute bands, never percentiles; percentiles are computed afterwards and shown alongside.
- Pillar score = weighted mean of the metrics that are present. Pillar coverage = present weight / total weight. Overall coverage = pillar-weighted mean of pillar coverages. Total = pillar-weighted mean over the pillars that have a score.
- A company below `universe.min_coverage` is `insufficient_data`, not ranked.
- Red flags sit outside the score.

---

### Task 11: Scoring config model and band interpolation

> **Changed by Task 30 (`09-hardening.md`):** tests no longer read the owner's `config/scoring.yaml`. Create `tests/frozen/scoring.yaml` (a copy) here; `test_the_shipped_config_...` becomes `test_the_owners_config_parses_and_scores_every_known_metric` (shape only) plus `test_the_frozen_config_has_the_values_the_tests_rely_on`.

**Files:**
- Create: `config/scoring.yaml`, `src/dgi/scoring/__init__.py`, `src/dgi/scoring/config.py`, `src/dgi/scoring/bands.py`
- Test: `tests/test_scoring_config.py`, `tests/test_scoring_bands.py`

**Interfaces:**
- Consumes: `dgi.errors.ConfigError`, `dgi.metrics.params.MetricParams`.
- Produces:
  - `PILLARS = ("dividend", "safety", "growth", "valuation")`, `KNOWN_METRICS: frozenset[str]` (the 26 metric names; every one is a `metrics_current` column except `margin_of_safety`, which the score stage derives from the fair value).
  - Pydantic models `Band(pillar, weight, points)`, `Universe(min_dividend_years=5, max_fundamentals_age_days=730, min_coverage=0.6, excluded_sic_ranges)`, `SectorRule(group, sic)`, `HardFilters(min_streak=5, max_payout_fcf=1.0, max_payout_eps=None, min_market_cap=1e9, min_yield=0.0, max_yield=None, min_score=0.0)`, `ValuationParams(required_return=0.09, growth_cap=0.06, growth_floor=0.0, range_delta=0.01)`, `ScoringConfig(version, pillars, metrics, universe, hard_filters, valuation, sector_groups, bands)`.
  - `parse_scoring_config(text: str) -> ScoringConfig`, `load_scoring_config(path: Path) -> ScoringConfig`; both raise `ConfigError`.
  - `band_score(points: list[tuple[float, float]], x: float) -> float`, `percentile_rank(sorted_values: list[float], x: float) -> float` (share of values at or below `x`, 0-100).

**Owner checkpoint (open decision 3 in the spec):** `config/scoring.yaml` below holds the starting bands, weights, hard-filter defaults, excluded SIC ranges and the sector map. The bands are adapted from `../dividend-growth-analysis/utils/valuation.py`; the rest are new. They are config, not code: the owner reads and edits them before or after implementation without touching Python.

- [ ] **Step 1: Write the failing tests**

`tests/test_scoring_config.py` (UNIT: config parsing, validation, defaults):
```python
from pathlib import Path

import pytest

from dgi.errors import ConfigError
from dgi.metrics.params import MetricParams
from dgi.scoring.config import KNOWN_METRICS, PILLARS, load_scoring_config, parse_scoring_config

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"

MINIMAL = """
pillars: {dividend: 1, safety: 1, growth: 1, valuation: 1}
bands:
  streak: {pillar: dividend, weight: 1, points: [[0, 0], [10, 100]]}
  payout_fcf: {pillar: safety, weight: 1, points: [[0, 100], [1, 0]]}
  eps_cagr_5: {pillar: growth, weight: 1, points: [[0, 0], [0.1, 100]]}
  pe: {pillar: valuation, weight: 1, points: [[10, 100], [30, 0]]}
"""


def test_the_shipped_config_parses_and_scores_every_known_metric():
    cfg = load_scoring_config(REPO_CONFIG)
    assert set(cfg.bands) == KNOWN_METRICS
    assert sum(cfg.pillars.values()) == pytest.approx(1.0)
    assert {b.pillar for b in cfg.bands.values()} == set(PILLARS)
    assert cfg.metrics == MetricParams()
    assert cfg.universe.min_coverage == 0.6 and cfg.hard_filters.min_streak == 5
    assert (6798, 6798) in cfg.universe.excluded_sic_ranges


def test_a_minimal_config_gets_the_documented_defaults():
    cfg = parse_scoring_config(MINIMAL)
    assert cfg.metrics.cut_tolerance == 0.01 and cfg.hard_filters.min_market_cap == 1e9
    assert cfg.valuation.required_return == 0.09 and cfg.universe.min_dividend_years == 5


def test_the_metrics_section_overrides_the_history_parameters():
    cfg = parse_scoring_config(MINIMAL + "metrics: {cut_tolerance: 0.02}\n")
    assert cfg.metrics.cut_tolerance == 0.02


@pytest.mark.parametrize(
    "text, message",
    [
        (MINIMAL.replace("[[0, 0], [10, 100]]", "[[0, 0]]"), "at least two points"),
        (MINIMAL.replace("[[0, 0], [10, 100]]", "[[5, 0], [5, 100]]"), "strictly increasing"),
        (MINIMAL.replace("[[0, 0], [10, 100]]", "[[0, 0], [10, 120]]"), "between 0 and 100"),
        (MINIMAL.replace("streak:", "streek:"), "unknown metric in bands: streek"),
        (MINIMAL.replace("pillar: dividend", "pillar: income"), "unknown pillar"),
        (MINIMAL.replace("weight: 1, points: [[0, 0], [10, 100]]", "weight: 0, points: [[0, 0], [10, 100]]"), "greater than 0"),
        (MINIMAL.replace("pillars: {dividend: 1, safety: 1, growth: 1, valuation: 1}", "pillars: {dividend: 0, safety: 0, growth: 0, valuation: 0}"), "not all zero"),
        (MINIMAL.replace("pillars: {dividend: 1, safety: 1, growth: 1, valuation: 1}", "pillars: {dividend: 1, safety: 1}"), "each of dividend"),
        (MINIMAL.replace("  streak: {pillar: dividend, weight: 1, points: [[0, 0], [10, 100]]}\n", ""), "pillar dividend has weight but no metric bands"),
        ("pillars: [", "invalid scoring config"),
    ],
)
def test_invalid_configs_raise_a_config_error_that_says_why(text, message):
    with pytest.raises(ConfigError, match=message):
        parse_scoring_config(text)


def test_a_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="cannot read scoring config"):
        load_scoring_config(tmp_path / "nope.yaml")
```

`tests/test_scoring_bands.py` (UNIT: band_score and percentile_rank):
```python
import pytest

from dgi.scoring.bands import band_score, percentile_rank

POINTS = [(0.0, 0.0), (10.0, 50.0), (20.0, 100.0)]


@pytest.mark.parametrize("x, expected", [(-5, 0), (0, 0), (5, 25), (10, 50), (15, 75), (20, 100), (99, 100)])
def test_band_score_interpolates_linearly_and_clamps_at_the_ends(x, expected):
    assert band_score(POINTS, x) == pytest.approx(expected)


def test_a_falling_band_works_for_lower_is_better_metrics():
    points = [(0.0, 100.0), (1.0, 0.0)]
    assert band_score(points, 0.25) == pytest.approx(75)


def test_percentile_rank_is_the_share_of_values_at_or_below():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile_rank(values, 3.0) == 75.0
    assert percentile_rank(values, 0.5) == 0.0
    assert percentile_rank(values, 9.0) == 100.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scoring_config.py tests/test_scoring_bands.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.scoring'`.

- [ ] **Step 3: Write the config file and the code**

`config/scoring.yaml`:
```yaml
version: 1

# Weight of each pillar in the total score. Missing pillars are left out and the rest re-weighted.
pillars:
  dividend: 0.30
  safety: 0.30
  growth: 0.20
  valuation: 0.20

# Stage 1 (needs a refresh from the API when changed): how dividend history is read.
metrics:
  raise_tolerance: 0.001   # a raise is more than this fraction above the prior year
  cut_tolerance: 0.01      # a cut is more than this fraction below the prior year
  special_ratio: 1.5       # an extra payment this many times the median regular one is special
  ttm_min_span_days: 240   # four quarters must span 240-300 days between first and last period end
  ttm_max_span_days: 300
  payout_cap: 9.99         # payout ratios are capped at 999%; set to the cap when the denominator is not positive
  ratio_cap: 99.0          # coverage and leverage ratios are capped here

universe:
  min_dividend_years: 5
  max_fundamentals_age_days: 730
  min_coverage: 0.6
  excluded_sic_ranges: [[6000, 6199], [6300, 6411], [6798, 6798]]

hard_filters:
  min_streak: 5
  max_payout_fcf: 1.0
  min_market_cap: 1000000000
  min_yield: 0.0

valuation:
  required_return: 0.09
  growth_cap: 0.06
  growth_floor: 0.0
  range_delta: 0.01

sector_groups:
  - {group: Health Care, sic: [[2830, 2836], [3840, 3851], [5122, 5122], [5912, 5912], [8000, 8099]]}
  - {group: Energy, sic: [[1200, 1399], [2900, 2999], [4610, 4619], [5171, 5172]]}
  - {group: Utilities, sic: [[4900, 4991]]}
  - {group: Communication, sic: [[4800, 4899], [2710, 2799], [7310, 7319]]}
  - {group: Technology, sic: [[3570, 3579], [3600, 3699], [7370, 7379], [3820, 3829]]}
  - {group: Consumer Staples, sic: [[100, 999], [2000, 2199], [2840, 2844], [5140, 5149], [5400, 5499]]}
  - {group: Materials, sic: [[1000, 1199], [1400, 1499], [2200, 2299], [2600, 2699], [2800, 2829], [2845, 2899], [3200, 3399]]}
  - {group: Industrials, sic: [[1500, 1799], [3400, 3569], [3700, 3799], [4000, 4799], [5000, 5099]]}
  - {group: Consumer Discretionary, sic: [[2300, 2399], [2500, 2599], [3000, 3199], [3630, 3659], [5200, 5399], [5500, 5999], [7000, 7299], [7800, 7999]]}

# Each metric maps to 0-100 by linear interpolation between points (x, score), clamped at the ends.
# weight is the metric's weight inside its pillar. Starting values adapted from
# ../dividend-growth-analysis/utils/valuation.py; owner-edited.
bands:
  streak:                  {pillar: dividend, weight: 3.0, points: [[0, 0], [5, 50], [10, 75], [25, 100]]}
  no_cut_streak:           {pillar: dividend, weight: 1.0, points: [[0, 0], [10, 50], [25, 100]]}
  dgr_1:                   {pillar: dividend, weight: 1.0, points: [[-0.01, 0], [0.0, 20], [0.05, 70], [0.10, 100]]}
  dgr_3:                   {pillar: dividend, weight: 1.5, points: [[0.0, 0], [0.03, 50], [0.07, 85], [0.10, 100]]}
  dgr_5:                   {pillar: dividend, weight: 2.0, points: [[0.0, 0], [0.03, 50], [0.07, 85], [0.10, 100]]}
  dgr_10:                  {pillar: dividend, weight: 1.5, points: [[0.0, 0], [0.03, 50], [0.07, 85], [0.10, 100]]}
  yield_vs_avg:            {pillar: dividend, weight: 1.0, points: [[-0.3, 0], [0.0, 50], [0.3, 100]]}
  payment_frequency:       {pillar: dividend, weight: 0.5, points: [[1, 40], [2, 70], [4, 100]]}
  payout_fcf:              {pillar: safety, weight: 4.0, points: [[0.0, 100], [0.5, 100], [0.75, 75], [1.0, 40], [1.25, 10], [2.0, 0]]}
  payout_fcf_5y:           {pillar: safety, weight: 2.0, points: [[0.0, 100], [0.5, 100], [0.75, 75], [1.0, 40], [1.25, 10], [2.0, 0]]}
  payout_earnings:         {pillar: safety, weight: 2.0, points: [[0.0, 100], [0.4, 100], [0.6, 85], [0.75, 65], [1.0, 40], [1.25, 10], [2.0, 0]]}
  payout_earnings_5y:      {pillar: safety, weight: 1.0, points: [[0.0, 100], [0.4, 100], [0.6, 85], [0.75, 65], [1.0, 40], [1.25, 10], [2.0, 0]]}
  interest_coverage:       {pillar: safety, weight: 2.0, points: [[1, 0], [3, 40], [6, 75], [12, 100]]}
  net_debt_ebitda:         {pillar: safety, weight: 2.0, points: [[0, 100], [1, 100], [2, 80], [3, 55], [5, 25], [8, 0]]}
  current_ratio:           {pillar: safety, weight: 1.0, points: [[0.5, 0], [1.0, 50], [1.5, 80], [2.0, 100]]}
  positive_earnings_years: {pillar: safety, weight: 2.0, points: [[5, 0], [8, 60], [10, 100]]}
  rev_cagr_5:              {pillar: growth, weight: 2.0, points: [[-0.02, 0], [0.0, 30], [0.04, 70], [0.08, 100]]}
  eps_cagr_5:              {pillar: growth, weight: 3.0, points: [[-0.02, 0], [0.0, 30], [0.05, 70], [0.10, 100]]}
  fcf_ps_cagr_5:           {pillar: growth, weight: 3.0, points: [[-0.02, 0], [0.0, 30], [0.05, 70], [0.10, 100]]}
  roe:                     {pillar: growth, weight: 2.0, points: [[0.0, 0], [0.08, 40], [0.15, 80], [0.25, 100]]}
  op_margin_std:           {pillar: growth, weight: 1.0, points: [[0.0, 100], [0.03, 80], [0.08, 40], [0.15, 0]]}
  share_trend_5:           {pillar: growth, weight: 1.0, points: [[-0.03, 100], [0.0, 60], [0.02, 20], [0.05, 0]]}
  pe:                      {pillar: valuation, weight: 2.0, points: [[8, 100], [15, 85], [20, 60], [28, 30], [40, 0]]}
  p_fcf:                   {pillar: valuation, weight: 2.0, points: [[8, 100], [15, 85], [22, 60], [30, 30], [45, 0]]}
  fcf_yield:               {pillar: valuation, weight: 2.0, points: [[0.02, 0], [0.04, 50], [0.06, 80], [0.08, 100]]}
  margin_of_safety:        {pillar: valuation, weight: 3.0, points: [[-0.5, 0], [0.0, 50], [0.25, 100]]}
```

`src/dgi/scoring/__init__.py`:
```python
"""Scoring: config model, bands, pillars, fair value, flags, and the score stage over the cache."""
```

`src/dgi/scoring/config.py`:
```python
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

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


class Band(BaseModel):
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


class Universe(BaseModel):
    min_dividend_years: int = 5
    max_fundamentals_age_days: int = 730
    min_coverage: float = Field(default=0.6, ge=0, le=1)
    excluded_sic_ranges: list[tuple[int, int]] = [(6000, 6199), (6300, 6411), (6798, 6798)]


class SectorRule(BaseModel):
    group: str
    sic: list[tuple[int, int]]


class HardFilters(BaseModel):
    min_streak: int = 5
    max_payout_fcf: float | None = 1.0
    max_payout_eps: float | None = None
    min_market_cap: float = 1e9
    min_yield: float = 0.0
    max_yield: float | None = None
    min_score: float = 0.0


class ValuationParams(BaseModel):
    required_return: float = Field(default=0.09, gt=0)
    growth_cap: float = Field(default=0.06, ge=0)
    growth_floor: float = Field(default=0.0, ge=0)
    range_delta: float = Field(default=0.01, ge=0)


class ScoringConfig(BaseModel):
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
```

`src/dgi/scoring/bands.py`:
```python
from __future__ import annotations

from bisect import bisect_right


def band_score(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    i = bisect_right([p[0] for p in points], x)
    (x0, y0), (x1, y1) = points[i - 1], points[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def percentile_rank(sorted_values: list[float], x: float) -> float:
    return 100.0 * bisect_right(sorted_values, x) / len(sorted_values)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scoring_config.py tests/test_scoring_bands.py`
Expected: PASS (23 passed).

- [ ] **Step 5: Commit**

```bash
git add config/scoring.yaml src/dgi/scoring tests/test_scoring_config.py tests/test_scoring_bands.py
git commit -m "feat: scoring config model with validated bands and interpolation"
```

---

### Task 12: Gordon fair-value range and sensitivity grid

**Files:**
- Create: `src/dgi/scoring/valuation.py`
- Test: `tests/test_scoring_valuation.py`

**Interfaces:**
- Consumes: `ValuationParams` (Task 11).
- Produces:
  - `FairValue(low, mid, high)` (frozen dataclass, each `float | None`).
  - `gordon_value(dps, growth, required_return) -> float | None` (`dps * (1 + growth) / (required_return - growth)`; None when `dps <= 0` or `required_return <= growth`).
  - `base_growth(dgr_5, dgr_3, params) -> float | None` (the 5-year dividend growth, else the 3-year, clamped to `[growth_floor, growth_cap]`).
  - `fair_value_range(dps, growth, params) -> FairValue | None`: `mid` at (return, growth); `low` at (return + delta, growth - delta, floored); `high` at (return - delta, growth). A bound that breaks the model is None, not an error.
  - `margin_of_safety(fair_mid, price) -> float | None` (`fair_mid / price - 1`).
  - `grid_axes(params) -> tuple[list[float], list[float]]` (required returns `r-2pt..r+2pt`; growths `0, 2%, ... growth_cap`), `sensitivity_grid(dps, returns, growths) -> list[list[float | None]]` (rows = returns, columns = growths).

- [ ] **Step 1: Write the failing tests**

`tests/test_scoring_valuation.py` (UNIT: fair value, margin of safety, sensitivity grid):
```python
import pytest

from dgi.scoring.config import ValuationParams
from dgi.scoring.valuation import base_growth, fair_value_range, gordon_value, grid_axes, margin_of_safety, sensitivity_grid

P = ValuationParams(required_return=0.09, growth_cap=0.06, growth_floor=0.0, range_delta=0.01)


def test_gordon_value():
    assert gordon_value(2.0, 0.05, 0.09) == pytest.approx(2.0 * 1.05 / 0.04)
    assert gordon_value(2.0, 0.09, 0.09) is None  # required return must exceed growth
    assert gordon_value(0.0, 0.03, 0.09) is None


def test_growth_is_the_five_year_rate_falling_back_to_three_and_is_clamped():
    assert base_growth(0.04, 0.10, P) == 0.04
    assert base_growth(None, 0.03, P) == 0.03
    assert base_growth(0.20, None, P) == 0.06   # cap
    assert base_growth(-0.05, None, P) == 0.0   # floor
    assert base_growth(None, None, P) is None


def test_the_range_runs_from_a_harsher_to_a_kinder_assumption():
    fv = fair_value_range(2.0, 0.05, P)
    assert fv.mid == pytest.approx(2.0 * 1.05 / 0.04)
    assert fv.low == pytest.approx(2.0 * 1.04 / 0.06)    # growth - 1pt, return + 1pt
    assert fv.high == pytest.approx(2.0 * 1.05 / 0.03)   # return - 1pt
    assert fv.low < fv.mid < fv.high


def test_a_bound_that_breaks_the_model_is_none_not_an_error():
    tight = ValuationParams(required_return=0.06, growth_cap=0.06, range_delta=0.01)
    fv = fair_value_range(2.0, 0.06, tight)
    assert fv.mid is None and fv.high is None  # return equals growth at the middle and is below it at the high end
    assert fv.low == pytest.approx(2.0 * 1.05 / 0.02)  # growth - 1pt against return + 1pt still works


@pytest.mark.parametrize("dps, growth", [(None, 0.03), (0.0, 0.03), (-1.0, 0.03), (2.0, None)])
def test_no_fair_value_without_a_positive_dividend_and_a_growth_rate(dps, growth):
    assert fair_value_range(dps, growth, P) is None


def test_margin_of_safety():
    assert margin_of_safety(60.0, 40.0) == pytest.approx(0.5)
    assert margin_of_safety(None, 40.0) is None
    assert margin_of_safety(60.0, 0.0) is None


def test_the_sensitivity_grid_has_the_documented_axes_and_cells():
    returns, growths = grid_axes(P)
    assert returns == [0.07, 0.08, 0.09, 0.10, 0.11] and growths == [0.0, 0.02, 0.04, 0.06]
    grid = sensitivity_grid(2.0, returns, growths)
    assert len(grid) == 5 and len(grid[0]) == 4
    assert grid[2][2] == pytest.approx(2.0 * 1.04 / 0.05)
    assert grid[0][3] == pytest.approx(2.0 * 1.06 / 0.01)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scoring_valuation.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.scoring.valuation'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/scoring/valuation.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from dgi.scoring.config import ValuationParams


@dataclass(frozen=True)
class FairValue:
    low: float | None
    mid: float | None
    high: float | None


def gordon_value(dps: float, growth: float, required_return: float) -> float | None:
    if dps <= 0 or required_return <= growth:
        return None
    return dps * (1 + growth) / (required_return - growth)


def base_growth(dgr_5: float | None, dgr_3: float | None, params: ValuationParams) -> float | None:
    growth = dgr_5 if dgr_5 is not None else dgr_3
    if growth is None:
        return None
    return min(max(growth, params.growth_floor), params.growth_cap)


def fair_value_range(dps: float | None, growth: float | None, params: ValuationParams) -> FairValue | None:
    if dps is None or growth is None or dps <= 0:
        return None
    r, d = params.required_return, params.range_delta
    low_growth = max(growth - d, params.growth_floor)
    return FairValue(
        low=gordon_value(dps, low_growth, r + d),
        mid=gordon_value(dps, growth, r),
        high=gordon_value(dps, growth, r - d),
    )


def margin_of_safety(fair_mid: float | None, price: float | None) -> float | None:
    if fair_mid is None or price is None or price <= 0:
        return None
    return fair_mid / price - 1


def grid_axes(params: ValuationParams) -> tuple[list[float], list[float]]:
    returns = [round(params.required_return + k * 0.01, 4) for k in (-2, -1, 0, 1, 2)]
    steps = int(round(params.growth_cap / 0.02))
    growths = [round(i * 0.02, 4) for i in range(steps + 1)]
    return returns, growths


def sensitivity_grid(dps: float, returns: list[float], growths: list[float]) -> list[list[float | None]]:
    return [[gordon_value(dps, g, r) for g in growths] for r in returns]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scoring_valuation.py`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/scoring/valuation.py tests/test_scoring_valuation.py
git commit -m "feat: Gordon-growth fair value range and sensitivity grid"
```

---

### Task 13: Pillar scoring and red flags

**Files:**
- Create: `src/dgi/scoring/pillars.py`, `src/dgi/scoring/flags.py`
- Test: `tests/test_scoring_pillars.py`, `tests/test_scoring_flags.py`

**Interfaces:**
- Consumes: `ScoringConfig`, `PILLARS`, `band_score`.
- Produces:
  - `MetricScore(metric, pillar, value, band_score, weight, contribution)` and `CompanyScore(pillars: dict[str, float | None], pillar_coverage: dict[str, float], coverage: float, total: float | None, details: list[MetricScore])` (frozen dataclasses).
  - `score_company(values: Mapping[str, float | None], cfg: ScoringConfig) -> CompanyScore`. `contribution` is the metric's share of the total, so contributions sum to `total`. Pillars with weight 0 never enter the total or the details.
  - `Flag(code, severity, text)` and `compute_flags(m: Mapping[str, Any]) -> list[Flag]` over a `metrics_current` row. Red: `dividend_cut`, `payout_eps_over_100`, `payout_fcf_over_100` (payout strictly above 100%), `negative_fcf`, `suspect_dividend`. Info: `special_dividend`, `irregular_payments`. Missing values never raise a flag.

- [ ] **Step 1: Write the failing tests**

`tests/test_scoring_pillars.py` (UNIT: score_company):
```python
import pytest

from dgi.scoring.config import parse_scoring_config
from dgi.scoring.pillars import score_company

CFG = parse_scoring_config("""
pillars: {dividend: 0.5, safety: 0.5, growth: 0, valuation: 0}
bands:
  streak:       {pillar: dividend, weight: 3, points: [[0, 0], [10, 100]]}
  dgr_5:        {pillar: dividend, weight: 1, points: [[0, 0], [0.1, 100]]}
  payout_fcf:   {pillar: safety,   weight: 1, points: [[0, 100], [1, 0]]}
  current_ratio: {pillar: safety,  weight: 1, points: [[0, 0], [2, 100]]}
  pe:           {pillar: valuation, weight: 1, points: [[10, 100], [30, 0]]}
""")


def test_pillar_score_is_the_weighted_mean_of_the_present_metrics():
    r = score_company({"streak": 10, "dgr_5": 0.0, "payout_fcf": 0.5, "current_ratio": 2.0}, CFG)
    assert r.pillars["dividend"] == pytest.approx((3 * 100 + 1 * 0) / 4)
    assert r.pillars["safety"] == pytest.approx((50 + 100) / 2)
    assert r.pillar_coverage["dividend"] == 1.0 and r.pillar_coverage["safety"] == 1.0


def test_a_missing_metric_lowers_coverage_and_is_left_out_of_the_mean():
    r = score_company({"streak": 10, "dgr_5": None, "payout_fcf": 0.5}, CFG)
    assert r.pillars["dividend"] == 100.0
    assert r.pillar_coverage["dividend"] == pytest.approx(3 / 4)
    assert r.pillar_coverage["safety"] == pytest.approx(1 / 2)
    assert r.coverage == pytest.approx(0.5 * 3 / 4 + 0.5 * 1 / 2)  # pillars with weight 0 add nothing


def test_the_total_reweights_over_the_pillars_that_have_a_score():
    r = score_company({"streak": 10, "dgr_5": 0.1}, CFG)  # safety has nothing
    assert r.pillars["safety"] is None
    assert r.total == pytest.approx(100.0)  # only dividend counts


def test_contributions_add_up_to_the_total():
    r = score_company({"streak": 5, "dgr_5": 0.05, "payout_fcf": 0.25, "current_ratio": 1.0}, CFG)
    assert sum(d.contribution for d in r.details) == pytest.approx(r.total)
    assert {d.metric for d in r.details} == {"streak", "dgr_5", "payout_fcf", "current_ratio"}


def test_a_pillar_with_zero_weight_never_enters_the_total_or_the_details():
    r = score_company({"streak": 10, "dgr_5": 0.1, "pe": 10}, CFG)
    assert r.pillars["valuation"] == 100.0
    assert r.total == pytest.approx(100.0)
    assert "pe" not in {d.metric for d in r.details}


def test_nothing_present_gives_no_total():
    r = score_company({}, CFG)
    assert r.total is None and r.coverage == 0.0 and r.details == []
```

`tests/test_scoring_flags.py` (UNIT: compute_flags):
```python
from dgi.scoring.flags import compute_flags


def codes(row):
    return [(f.code, f.severity) for f in compute_flags(row)]


def test_a_clean_company_has_no_flags():
    assert compute_flags({"cut_years_5y": 0, "payout_earnings": 0.5, "payout_fcf": 0.6, "fcf_latest": 10.0}) == []


def test_every_red_flag_fires_with_words_the_company_page_can_show():
    row = {"cut_years_5y": 1, "payout_earnings": 1.4, "payout_fcf": 9.99, "fcf_latest": -5.0, "suspect_dividend_count": 2}
    assert codes(row) == [
        ("dividend_cut", "red"), ("payout_eps_over_100", "red"), ("payout_fcf_over_100", "red"),
        ("negative_fcf", "red"), ("suspect_dividend", "red"),
    ]
    text = {f.code: f.text for f in compute_flags(row)}
    assert "140%" in text["payout_eps_over_100"] and "free cash flow" in text["payout_fcf_over_100"]


def test_a_payout_of_exactly_100_percent_is_not_flagged():
    assert compute_flags({"payout_fcf": 1.0}) == []


def test_missing_values_never_raise_a_flag():
    assert compute_flags({}) == []
    assert compute_flags({"cut_years_5y": None, "payout_fcf": None, "fcf_latest": None}) == []


def test_specials_and_irregular_payment_counts_are_information_not_red():
    assert codes({"special_count_5y": 1, "irregular_payments": True}) == [("special_dividend", "info"), ("irregular_payments", "info")]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scoring_pillars.py tests/test_scoring_flags.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.scoring.pillars'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/scoring/pillars.py`:
```python
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
```

`src/dgi/scoring/flags.py`:
```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PAYOUT_LIMIT = 1.0


@dataclass(frozen=True)
class Flag:
    code: str
    severity: str
    text: str


def compute_flags(m: Mapping[str, Any]) -> list[Flag]:
    flags: list[Flag] = []
    if (m.get("cut_years_5y") or 0) > 0:
        flags.append(Flag("dividend_cut", "red", "The dividend was cut or suspended in the last 5 years."))
    for key, code, label in (("payout_earnings", "payout_eps_over_100", "earnings"), ("payout_fcf", "payout_fcf_over_100", "free cash flow")):
        payout = m.get(key)
        if payout is not None and payout > PAYOUT_LIMIT:
            flags.append(Flag(code, "red", f"Dividends paid exceed {label} (payout {payout:.0%})."))
    fcf = m.get("fcf_latest")
    if fcf is not None and fcf < 0:
        flags.append(Flag("negative_fcf", "red", "Free cash flow was negative in the latest fiscal year."))
    if (m.get("suspect_dividend_count") or 0) > 0:
        flags.append(Flag("suspect_dividend", "red", "A dividend in the price data is at or above the share price; likely a data error."))
    if (m.get("special_count_5y") or 0) > 0:
        flags.append(Flag("special_dividend", "info", "A special dividend in the last 5 years is excluded from the growth figures."))
    if m.get("irregular_payments"):
        flags.append(Flag("irregular_payments", "info", "The number of payments last year differs from the usual count; growth figures may be distorted."))
    return flags
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scoring_pillars.py tests/test_scoring_flags.py`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/scoring/pillars.py src/dgi/scoring/flags.py tests/test_scoring_pillars.py tests/test_scoring_flags.py
git commit -m "feat: pillar scoring with coverage and red flags"
```

---

### Task 14: Score stage over the cache

> **Changed by Task 30 (`09-hardening.md`):** `tests/cache_fixtures.py` defines `FROZEN_SCORING = tests/frozen/scoring.yaml` and loads `CFG` from it, not from `config/scoring.yaml`.

Ties the pieces together: universe reasons (banks, insurers and REITs are listed with a reason, never scored), sector group, fair value, pillar scoring, percentiles and flags, written to `scores`, `score_detail`, `flags`.

**Files:**
- Create: `src/dgi/scoring/stage.py`
- Test: `tests/cache_fixtures.py` (shared by Tasks 14-22; Task 15 adds `make_meta` and `persist`), `tests/test_scoring_stage.py`

**Interfaces:**
- Consumes: tables `company_dim`, `metrics_current` (Task 10), everything in Tasks 11-13, `dgi.schema.recreate_tables` / `insert_rows`.
- Produces:
  - `ScoreResult(companies: int, scored: int, not_scored: dict[str, int])` (reason -> count, including `insufficient_data` from low coverage).
  - `score_cache(con, cfg: ScoringConfig, today: date) -> ScoreResult`: recreates `scores`, `score_detail`, `flags`; `scores.status` is `scored` or `not_scored` with a `reason` (`financial_or_reit`, `no_price`, `short_dividend_history`, `insufficient_data`) and `sector_group` for every company; detail and flags exist only for scored companies.
  - `universe_reason(row, today, universe) -> str | None` (checked in that order), `sector_group(sic, rules) -> str` (first matching rule, else `"Other"`), `sic_number`, `in_ranges`, `SCORE_TABLES`.

- [ ] **Step 1: Write the failing tests**

`cache_with(companies)` builds a cache connection with every table and the given `company_dim` / `metrics_current` rows (a healthy dividend grower's values, overridable per test), so scoring and cache tests need no pipeline.

`tests/cache_fixtures.py` (shared fixtures):
```python
"""Hand-built cache contents for the scoring, cache and web tests: no API, no network."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import duckdb

from dgi import schema
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
```

`tests/test_scoring_stage.py` (UNIT: universe reasons, sector groups, score_cache):
```python
import datetime as dt

import pytest

from dgi.scoring.config import SectorRule, Universe
from dgi.scoring.stage import in_ranges, score_cache, sector_group, sic_number, universe_reason
from tests.cache_fixtures import CFG, GOOD, TODAY, cache_with

def test_sic_helpers():
    assert sic_number("2080") == 2080 and sic_number(None) is None and sic_number("x1") is None
    assert in_ranges(6021, [(6000, 6199)]) and not in_ranges(2080, [(6000, 6199)]) and not in_ranges(None, [(0, 9999)])


def test_sector_group_uses_the_first_matching_rule_else_other():
    rules = [SectorRule(group="Energy", sic=[(1200, 1399)]), SectorRule(group="Materials", sic=[(1000, 1499)])]
    assert sector_group("1311", rules) == "Energy"
    assert sector_group("1400", rules) == "Materials"
    assert sector_group("9999", rules) == "Other" and sector_group(None, rules) == "Other"


@pytest.mark.parametrize(
    "sic, over, expected",
    [
        ("2080", {}, None),
        ("6021", {}, "financial_or_reit"),        # bank
        ("6331", {}, "financial_or_reit"),        # insurer
        ("6798", {}, "financial_or_reit"),        # REIT
        ("6211", {}, None),                        # asset managers stay in
        ("2080", {"price": None}, "no_price"),
        ("2080", {"years_history": 4}, "short_dividend_history"),
        ("2080", {"years_history": None}, "short_dividend_history"),
        ("2080", {"fy_period_end": dt.date(2023, 1, 1)}, "insufficient_data"),
        ("2080", {"fy_period_end": None}, "insufficient_data"),
        ("6021", {"price": None}, "financial_or_reit"),  # the financial reason wins
    ],
)
def test_universe_reason_in_order(sic, over, expected):
    assert universe_reason({"sic": sic, **{**GOOD, **over}}, TODAY, Universe()) == expected


def table(con, sql):
    return con.execute(sql).fetchall()


def test_a_scored_company_has_a_total_pillars_fair_value_and_an_explanation():
    con = cache_with({"AAA": ("2080", {})})
    result = score_cache(con, CFG, TODAY)
    assert (result.companies, result.scored, result.not_scored) == (1, 1, {})
    s = con.execute("SELECT status, reason, sector_group, dividend, safety, growth, valuation, coverage, total, fair_value_mid, margin_of_safety FROM scores").fetchone()
    assert s[0] == "scored" and s[1] is None and s[2] == "Consumer Staples"
    assert all(0 <= v <= 100 for v in s[3:7]) and s[7] == pytest.approx(1.0) and 0 < s[8] < 100
    assert s[9] == pytest.approx(1.8 * 1.05 / (0.09 - 0.05)) and s[10] == pytest.approx(s[9] / 60.0 - 1)
    total_contrib, = con.execute("SELECT sum(contribution) FROM score_detail").fetchone()
    assert total_contrib == pytest.approx(s[8])
    assert con.execute("SELECT count(DISTINCT metric) FROM score_detail").fetchone() == (26,)


def test_not_scored_companies_keep_a_reason_and_get_no_detail_or_flags():
    con = cache_with({"BANK": ("6021", {}), "NEWC": ("2080", {"years_history": 3}), "NOPX": ("2080", {"price": None}), "AAA": ("2080", {})})
    result = score_cache(con, CFG, TODAY)
    assert result.scored == 1 and result.not_scored == {"financial_or_reit": 1, "short_dividend_history": 1, "no_price": 1}
    assert table(con, "SELECT ticker, status, reason FROM scores WHERE status = 'not_scored' ORDER BY ticker") == [
        ("BANK", "not_scored", "financial_or_reit"), ("NEWC", "not_scored", "short_dividend_history"), ("NOPX", "not_scored", "no_price")]
    assert table(con, "SELECT DISTINCT ticker FROM score_detail") == [("AAA",)]
    assert table(con, "SELECT total FROM scores WHERE ticker = 'BANK'") == [(None,)]


def test_a_company_below_minimum_coverage_is_insufficient_data_not_ranked():
    sparse = {k: None for k in GOOD if k not in ("price", "years_history", "fy_period_end", "streak", "dividend_ttm")}
    con = cache_with({"SPARSE": ("2080", sparse)})
    result = score_cache(con, CFG, TODAY)
    assert result.scored == 0 and result.not_scored == {"insufficient_data": 1}
    assert table(con, "SELECT status, reason, total FROM scores") == [("not_scored", "insufficient_data", None)]


def test_percentiles_rank_a_metric_among_the_scored_companies():
    con = cache_with({"LOW": ("2080", {"streak": 6}), "MID": ("2080", {"streak": 11}), "HIGH": ("2080", {"streak": 30})})
    score_cache(con, CFG, TODAY)
    got = dict(table(con, "SELECT ticker, percentile FROM score_detail WHERE metric = 'streak'"))
    assert got == {"LOW": pytest.approx(100 / 3), "MID": pytest.approx(200 / 3), "HIGH": 100.0}


def test_flags_are_written_for_scored_companies():
    con = cache_with({"AAA": ("2080", {"cut_years_5y": 1, "payout_fcf": 1.3})})
    score_cache(con, CFG, TODAY)
    assert table(con, "SELECT code, severity FROM flags ORDER BY code") == [("dividend_cut", "red"), ("payout_fcf_over_100", "red")]


def test_rescoring_replaces_the_previous_results():
    con = cache_with({"AAA": ("2080", {})})
    score_cache(con, CFG, TODAY)
    score_cache(con, CFG, TODAY)
    assert table(con, "SELECT count(*) FROM scores") == [(1,)]
    assert table(con, "SELECT count(*) FROM score_detail WHERE metric = 'streak'") == [(1,)]


def test_a_company_without_a_dividend_growth_rate_has_no_fair_value_but_can_still_score():
    con = cache_with({"AAA": ("2080", {"dgr_5": None, "dgr_3": None})})
    score_cache(con, CFG, TODAY)
    assert table(con, "SELECT status, fair_value_mid, margin_of_safety FROM scores") == [("scored", None, None)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scoring_stage.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.scoring.stage'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/scoring/stage.py`:
```python
"""The score stage: reads metrics_current and company_dim, writes scores, score_detail and flags."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from dgi.schema import insert_rows, recreate_tables
from dgi.scoring.bands import percentile_rank
from dgi.scoring.config import ScoringConfig, SectorRule, Universe
from dgi.scoring.flags import compute_flags
from dgi.scoring.pillars import score_company
from dgi.scoring.valuation import base_growth, fair_value_range, margin_of_safety

SCORE_TABLES = ("scores", "score_detail", "flags")
SECTOR_OTHER = "Other"


@dataclass(frozen=True)
class ScoreResult:
    companies: int
    scored: int
    not_scored: dict[str, int]


def sic_number(sic: str | None) -> int | None:
    return int(sic) if sic is not None and sic.strip().isdigit() else None


def in_ranges(value: int | None, ranges: Sequence[tuple[int, int]]) -> bool:
    return value is not None and any(low <= value <= high for low, high in ranges)


def sector_group(sic: str | None, rules: Sequence[SectorRule]) -> str:
    number = sic_number(sic)
    for rule in rules:
        if in_ranges(number, rule.sic):
            return rule.group
    return SECTOR_OTHER


def universe_reason(row: Mapping[str, Any], today: date, universe: Universe) -> str | None:
    """Why a company is not scored, or None when it is a candidate. Checked in this order."""
    if in_ranges(sic_number(row["sic"]), universe.excluded_sic_ranges):
        return "financial_or_reit"
    if row["price"] is None:
        return "no_price"
    years = row["years_history"]
    if years is None or years < universe.min_dividend_years:
        return "short_dividend_history"
    fy_end = row["fy_period_end"]
    if fy_end is None or (today - fy_end).days > universe.max_fundamentals_age_days:
        return "insufficient_data"
    return None


def load_rows(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cur = con.execute("SELECT c.sic, m.* FROM metrics_current m JOIN company_dim c ON c.ticker = m.ticker ORDER BY m.ticker")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, values)) for values in cur.fetchall()]


def _empty_score(row: Mapping[str, Any], status: str, reason: str | None, group: str) -> dict[str, Any]:
    return {"ticker": row["ticker"], "status": status, "reason": reason, "sector_group": group}


def _percentiles(details: list[dict[str, Any]]) -> None:
    by_metric: dict[str, list[float]] = defaultdict(list)
    for d in details:
        by_metric[d["metric"]].append(d["value"])
    ordered = {metric: sorted(values) for metric, values in by_metric.items()}
    for d in details:
        d["percentile"] = percentile_rank(ordered[d["metric"]], d["value"])


def score_cache(con: duckdb.DuckDBPyConnection, cfg: ScoringConfig, today: date) -> ScoreResult:
    """Replace scores, score_detail and flags from the metrics tables. Never touches the API."""
    scores: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    rows = load_rows(con)
    for row in rows:
        group = sector_group(row["sic"], cfg.sector_groups)
        reason = universe_reason(row, today, cfg.universe)
        if reason is not None:
            reasons[reason] += 1
            scores.append(_empty_score(row, "not_scored", reason, group))
            continue
        growth = base_growth(row["dgr_5"], row["dgr_3"], cfg.valuation)
        fair = fair_value_range(row["dividend_ttm"], growth, cfg.valuation)
        safety_margin = margin_of_safety(fair.mid if fair else None, row["price"])
        values = {metric: row.get(metric) for metric in cfg.bands if metric != "margin_of_safety"}
        values["margin_of_safety"] = safety_margin
        result = score_company(values, cfg)
        if result.total is None or result.coverage < cfg.universe.min_coverage:
            reasons["insufficient_data"] += 1
            scores.append(_empty_score(row, "not_scored", "insufficient_data", group))
            continue
        scores.append({
            **_empty_score(row, "scored", None, group), **result.pillars, "coverage": result.coverage, "total": result.total,
            "fair_value_low": fair.low if fair else None, "fair_value_mid": fair.mid if fair else None,
            "fair_value_high": fair.high if fair else None, "margin_of_safety": safety_margin,
        })
        details += [{"ticker": row["ticker"], "metric": d.metric, "pillar": d.pillar, "value": d.value, "band_score": d.band_score,
                     "weight": d.weight, "contribution": d.contribution} for d in result.details]
        flags += [{"ticker": row["ticker"], "code": f.code, "severity": f.severity, "text": f.text} for f in compute_flags(row)]
    _percentiles(details)
    recreate_tables(con, SCORE_TABLES)
    insert_rows(con, "scores", scores)
    insert_rows(con, "score_detail", details)
    insert_rows(con, "flags", flags)
    scored = sum(1 for s in scores if s["status"] == "scored")
    return ScoreResult(len(rows), scored, dict(reasons))
```

- [ ] **Step 4: Run to verify it passes, then the whole suite**

Run: `uv run pytest tests/test_scoring_stage.py`
Expected: PASS (20 passed).

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/dgi/scoring/stage.py tests/cache_fixtures.py tests/test_scoring_stage.py
git commit -m "feat: score stage writing scores, score detail and flags"
```
