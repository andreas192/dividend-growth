import datetime as dt

import pytest

from dgi.cache import AnnualData
from dgi.web.series import (
    ChartSeries, ChartSpec, SeriesCache, annual_charts, daily_charts, dividend_charts, fundamentals_charts, trailing_yield, yield_chart,
)

D = dt.date


def annual(**over):
    dividends = [{"year": y, "dps": 1.0 + 0.1 * (y - 2020), "n_payments": 4, "special_total": 0.0, "complete": y < 2026} for y in range(2020, 2027)]
    fundamentals = [
        {"fiscal_year": y, "eps_diluted": 3.0, "fcf_per_share": 3.5, "shares_diluted": 100e6 - 1e6 * (y - 2020), "operating_income": 200.0,
         "interest_expense": 10.0, "net_debt": 300.0, "ebitda": 250.0, "payout_earnings": 0.5, "payout_fcf": 0.45}
        for y in range(2021, 2026)
    ]
    return AnnualData(over.get("dividends", dividends), over.get("fundamentals", fundamentals),
                      over.get("prices", {y: 20.0 + y - 2020 for y in range(2020, 2026)}))


def by_id(charts):
    return {c.id: c for c in charts}


def test_dividend_charts_use_complete_years_and_compute_growth():
    charts = by_id(dividend_charts(annual()))
    assert charts["dps"].x == [2020, 2021, 2022, 2023, 2024, 2025] and charts["dps"].kind == "bar"
    growth = charts["dps_growth"].series[0].values
    assert growth[0] is None and growth[1] == pytest.approx(0.1) and growth[2] == pytest.approx(1.2 / 1.1 - 1)


def test_no_dividends_means_no_dividend_charts():
    assert dividend_charts(annual(dividends=[])) == []
    assert dividend_charts(annual(dividends=[{"year": 2026, "dps": 1.0, "n_payments": 1, "special_total": 0.0, "complete": False}])) == []


def test_yield_chart_divides_by_the_year_end_price_and_carries_the_average():
    chart = yield_chart(annual(), 0.03)
    assert chart.series[0].values[0] == pytest.approx(1.0 / 20.0) and chart.references == [{"name": "5-year average", "value": 0.03}]
    assert yield_chart(annual(), None).references == []
    assert yield_chart(annual(prices={}), 0.03) is None


def test_payout_values_above_200_percent_are_drawn_at_200_with_a_note():
    fundamentals = [{"fiscal_year": 2025, "eps_diluted": None, "fcf_per_share": None, "shares_diluted": None, "operating_income": None,
                     "interest_expense": None, "net_debt": None, "ebitda": None, "payout_earnings": 9.99, "payout_fcf": 0.8}]
    chart = by_id(fundamentals_charts(annual(fundamentals=fundamentals)))["payout"]
    assert chart.series[0].values == [2.0] and chart.series[1].values == [0.8]
    assert "above 200%" in chart.note and chart.references == [{"name": "100%", "value": 1.0}]


def test_leverage_coverage_and_shares_edge_cases():
    base = {"fiscal_year": 2025, "eps_diluted": 1.0, "fcf_per_share": 1.0, "shares_diluted": 50e6, "payout_earnings": None, "payout_fcf": None,
            "operating_income": 100.0, "interest_expense": 0.0, "net_debt": -50.0, "ebitda": 80.0}
    rows = [base, {**base, "fiscal_year": 2026, "interest_expense": 5.0, "net_debt": 100.0, "ebitda": -10.0},
            {**base, "fiscal_year": 2027, "operating_income": None, "net_debt": None}]
    charts = by_id(fundamentals_charts(annual(fundamentals=rows)))
    assert charts["leverage"].series[0].values == [0.0, 10.0, None]          # net cash, negative EBITDA drawn at the cap, unknown
    assert charts["coverage"].series[0].values == [50.0, 20.0, None]          # no interest expense draws at the cap
    assert charts["shares"].series[0].values == [50.0, 50.0, 50.0]


def test_charts_without_any_data_are_left_out():
    empty = [{"fiscal_year": 2025, **{k: None for k in ("eps_diluted", "fcf_per_share", "shares_diluted", "operating_income", "interest_expense", "net_debt", "ebitda", "payout_earnings", "payout_fcf")}}]
    assert fundamentals_charts(annual(fundamentals=empty)) == []
    assert fundamentals_charts(annual(fundamentals=[])) == []


def test_per_share_chart_matches_dividends_to_fiscal_years():
    chart = by_id(fundamentals_charts(annual()))["per_share"]
    assert [s.name for s in chart.series] == ["EPS", "Free cash flow per share", "Dividend per share"]
    assert [s.slot for s in chart.series] == [1, 2, 3]
    assert chart.series[2].values == [pytest.approx(1.1), pytest.approx(1.2), pytest.approx(1.3), pytest.approx(1.4), pytest.approx(1.5)]


def test_annual_charts_order_puts_the_yield_after_the_dividend_bars():
    ids = [c.id for c in annual_charts(annual(), 0.03)]
    assert ids[:3] == ["dps", "yield", "dps_growth"] and set(ids) >= {"payout", "per_share", "leverage", "coverage", "shares"}


def test_to_json_is_plain_data():
    spec = ChartSpec("x", "T", "line", "pct", [1, 2], [ChartSeries("S", [0.1, None], 2)], note="n", references=[{"name": "r", "value": 1.0}])
    assert spec.to_json() == {"id": "x", "title": "T", "kind": "line", "unit": "pct", "x": [1, 2], "xType": "category",
                              "series": [{"name": "S", "values": [0.1, None], "slot": 2}], "note": "n", "references": [{"name": "r", "value": 1.0}]}


# daily -----------------------------------------------------------------------------------------------------------------

PAYMENTS = [(D(2023, 3, 1), 0.25), (D(2023, 6, 1), 0.25), (D(2023, 9, 1), 0.25), (D(2023, 12, 1), 0.25), (D(2024, 3, 1), 0.30)]


def test_trailing_yield_sums_the_previous_365_days_and_waits_a_year_for_the_first_payment():
    points = [(D(2023, 12, 15), 50.0), (D(2024, 3, 2), 50.0), (D(2024, 3, 2) + dt.timedelta(days=365), 50.0), (D(2024, 6, 1), 0.0)]
    values = trailing_yield(points, PAYMENTS)
    assert values[0] is None                                    # less than a year after the first payment
    assert values[1] == pytest.approx((0.25 * 3 + 0.30) / 50.0)  # 2023-06, 09, 12 and 2024-03 (2023-03-01 is just outside the window)
    assert values[2] is None                                    # a year on, the last payment has left the window
    assert values[3] is None                                    # zero price


def test_trailing_yield_with_no_payments_is_all_none():
    assert trailing_yield([(D(2024, 1, 1), 10.0)], []) == [None]


def test_daily_charts_have_price_and_yield_with_the_average_line():
    points = [(D(2024, 4, 1) + dt.timedelta(days=i), 40.0 + i) for i in range(5)]
    charts = by_id(daily_charts(points, PAYMENTS, 0.03))
    assert charts["price"].x_type == "time" and charts["price"].x[0] == "2024-04-01" and charts["price"].series[0].values[0] == 40.0
    assert charts["yield_daily"].references == [{"name": "5-year average", "value": 0.03}]
    assert daily_charts([], PAYMENTS, None) == []
    assert "yield_daily" not in by_id(daily_charts(points, [], None))


# cache -----------------------------------------------------------------------------------------------------------------

def test_series_cache_loads_once_per_key_and_never_serves_another_upstream():
    cache, calls = SeriesCache(), []

    def loader(tag):
        def load():
            calls.append(tag)
            return [(D(2024, 1, 1), 1.0)]
        return load

    cache.get_or_load("u1", "KO", loader("a"))
    cache.get_or_load("u1", "KO", loader("b"))
    assert calls == ["a"]
    cache.get_or_load("u2", "KO", loader("c"))      # a new upstream key reloads and drops the old entries
    cache.get_or_load("u1", "KO", loader("d"))
    assert calls == ["a", "c", "d"]


def test_series_cache_evicts_the_oldest_entry_past_its_limit():
    cache, calls = SeriesCache(max_entries=2), []
    for ticker in ("A", "B", "C"):
        cache.get_or_load("u", ticker, lambda t=ticker: calls.append(t) or [])
    cache.get_or_load("u", "A", lambda: calls.append("A again") or [])
    assert calls == ["A", "B", "C", "A again"]


def test_a_failing_loader_caches_nothing():
    cache = SeriesCache()
    with pytest.raises(RuntimeError):
        cache.get_or_load("u", "A", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert cache.get_or_load("u", "A", lambda: [(D(2024, 1, 1), 2.0)]) == [(D(2024, 1, 1), 2.0)]
