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
