import pytest

from dgi.metrics.dividends import (
    DividendMetrics,
    build_dividend_metrics,
    compute_dividend_metrics,
    compute_streaks,
    count_cut_years,
    dividend_cagr,
    usual_count,
)
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import D, TODAY, add_dividends, add_split, metrics_con, quarterly

RAISE, CUT = 0.001, 0.01


def streaks(series, last_year):
    return compute_streaks(series, last_year, RAISE, CUT)


def test_every_year_a_raise_counts_back_to_the_start_of_the_history():
    series = {2020: 1.0, 2021: 1.1, 2022: 1.2, 2023: 1.3}
    assert streaks(series, 2023) == (3, 3)  # three steps: 2021, 2022, 2023


def test_a_flat_year_ends_the_increase_streak_but_not_the_no_cut_streak():
    series = {2019: 1.0, 2020: 1.1, 2021: 1.1, 2022: 1.2, 2023: 1.3}
    assert streaks(series, 2023) == (2, 4)


def test_a_change_inside_the_raise_tolerance_counts_as_flat():
    series = {2022: 1.0, 2023: 1.0005}  # +0.05% is under the 0.1% raise tolerance
    assert streaks(series, 2023) == (0, 1)


def test_a_cut_ends_both_streaks():
    series = {2019: 1.0, 2020: 1.1, 2021: 0.9, 2022: 1.0, 2023: 1.1}
    assert streaks(series, 2023) == (2, 2)


def test_a_small_dip_inside_the_cut_tolerance_is_flat_not_a_cut():
    series = {2022: 1.00, 2023: 0.995}  # -0.5% is inside the 1% cut tolerance
    assert streaks(series, 2023) == (0, 1)


def test_a_company_that_stopped_paying_has_no_streak_and_a_cut():
    series = {2018: 1.0, 2019: 1.1, 2020: 1.2, 2021: 1.3}  # nothing in 2022-2025
    assert streaks(series, 2025) == (0, 0)
    assert count_cut_years(series, 2025, 5, RAISE, CUT) >= 1


def test_a_gap_year_inside_the_history_stops_the_streak_there():
    series = {2019: 1.0, 2020: 1.1, 2022: 1.3, 2023: 1.4}  # 2021 missing
    assert streaks(series, 2023) == (1, 1)  # only 2023 vs 2022; 2022 vs 2021 has no prior year


def test_the_streak_never_exceeds_the_available_history():
    series = {2023: 1.0, 2024: 1.1}
    inc, no_cut = streaks(series, 2024)
    assert inc == 1 and no_cut == 1


def test_an_empty_series_has_no_streak():
    assert streaks({}, 2025) == (0, 0)


def test_dividend_cagr():
    series = {2020: 1.0, 2025: 2.0}
    assert dividend_cagr(series, 2025, 5) == pytest.approx(2 ** (1 / 5) - 1)
    assert dividend_cagr(series, 2025, 3) is None  # 2022 missing
    assert dividend_cagr({2024: 0.0, 2025: 1.0}, 2025, 1) is None  # cannot grow from zero


def test_cut_years_counts_cuts_and_suspensions_inside_the_window():
    series = {2019: 1.0, 2020: 0.5, 2021: 0.6, 2022: 0.7, 2023: 0.8, 2024: 0.9}
    assert count_cut_years(series, 2024, 5, RAISE, CUT) == 1  # 2020 is inside 2020-2024
    assert count_cut_years(series, 2024, 3, RAISE, CUT) == 0


def test_usual_count_is_the_most_common_with_ties_going_to_the_smaller():
    assert usual_count({2020: 4, 2021: 4, 2022: 5}) == 4
    assert usual_count({2020: 2, 2021: 4}) == 2
    assert usual_count({}) is None


def test_compute_dividend_metrics_collects_everything():
    series = {y: 1.0 * 1.05 ** (y - 2014) for y in range(2014, 2026)}
    counts = {y: 4 for y in series}
    counts[2025] = 5
    m = compute_dividend_metrics(series, counts, 2025, RAISE, CUT)
    assert (m.streak, m.no_cut_streak, m.years_history, m.cut_years_5y) == (11, 11, 12, 0)
    assert m.dgr_5 == pytest.approx(0.05) and m.dgr_10 == pytest.approx(0.05)
    assert m.payment_frequency == 5 and m.irregular_payments is True


def test_a_split_and_a_special_dividend_do_not_disturb_the_streak():
    con = metrics_con()
    payments = []
    for year in range(2018, 2026):
        amount = 0.25 * 1.06 ** (year - 2018)
        payments += quarterly(year, amount * (2.0 if year < 2022 else 1.0))  # paid on the pre-split basis before 2022
    payments.append((D(2023, 12, 20), 5.0))  # special
    add_dividends(con, "AAA", payments)
    add_split(con, "AAA", D(2022, 1, 3), 2.0)
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    assert build_dividend_metrics(con, TODAY, RAISE, CUT) == 1
    row = con.execute("SELECT streak, no_cut_streak, years_history, cut_years_5y, irregular_payments FROM tmp_dividend_metrics").fetchone()
    assert row == (7, 7, 8, 0, False)


def test_the_partial_current_year_is_left_out_of_the_series():
    con = metrics_con()
    add_dividends(con, "AAA", [p for y in range(2022, 2026) for p in quarterly(y, 0.25 * 1.1 ** (y - 2022))] + [(D(2026, 2, 10), 0.01)])
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    build_dividend_metrics(con, TODAY, RAISE, CUT)
    assert con.execute("SELECT streak, years_history FROM tmp_dividend_metrics").fetchone() == (3, 4)


def test_build_dividend_metrics_with_no_payers_creates_an_empty_table():
    con = metrics_con()
    assert build_dividend_metrics(con, TODAY, RAISE, CUT) == 0
    assert con.execute("SELECT count(*) FROM tmp_dividend_metrics").fetchone() == (0,)


def test_metrics_dataclass_is_frozen():
    m = DividendMetrics(1, 1, None, None, None, None, 2, 4, 0, False)
    with pytest.raises(Exception):
        m.streak = 2
