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
        (MINIMAL + "hard_filter: {min_streak: 3}\n", "hard_filter"),
        (MINIMAL + "hard_filters: {min_streek: 3}\n", "min_streek"),
        (MINIMAL + "universe: {min_coverge: 0.5}\n", "min_coverge"),
        (MINIMAL + "valuation: {required_retrun: 0.1}\n", "required_retrun"),
        (MINIMAL + "metrics: {cut_tolerence: 0.02}\n", "cut_tolerence"),
        (MINIMAL.replace("weight: 1, points: [[0, 0], [10, 100]]", "weight: 1, wieght: 2, points: [[0, 0], [10, 100]]"), "wieght"),
        (MINIMAL + "sector_groups: [{group: Energy, sic: [[1, 2]], extra: 1}]\n", "extra"),
    ],
)
def test_invalid_configs_raise_a_config_error_that_says_why(text, message):
    with pytest.raises(ConfigError, match=message):
        parse_scoring_config(text)


def test_a_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="cannot read scoring config"):
        load_scoring_config(tmp_path / "nope.yaml")
