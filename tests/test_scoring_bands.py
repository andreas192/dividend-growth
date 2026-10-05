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
