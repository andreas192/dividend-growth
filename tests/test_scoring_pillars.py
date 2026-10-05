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
