import datetime as dt

import pytest

from dgi.cache import load_annual, load_company, load_payments
from tests.cache_fixtures import scored_cache
from tests.web_fixtures import COMPANIES, add_history


@pytest.fixture(scope="module")
def con():
    c = scored_cache(COMPANIES)
    add_history(c, "AAA")
    return c


def test_a_scored_company_has_everything_the_page_needs(con):
    view = load_company(con, "AAA")
    assert (view.ticker, view.name, view.status, view.reason, view.sector_group) == ("AAA", "AAA Inc", "scored", None, "Consumer Staples")
    assert view.metrics["streak"] == 30 and view.score["total"] > 0
    assert [d["pillar"] for d in view.details] == sorted((d["pillar"] for d in view.details), key=("dividend", "safety", "growth", "valuation").index)
    contributions = [d["contribution"] for d in view.details if d["pillar"] == "dividend"]
    assert contributions == sorted(contributions, reverse=True)
    assert view.flags == []


def test_lookup_ignores_case_and_flags_come_red_first():
    con = scored_cache({"CCC": ("1311", {"payout_fcf": 1.1, "special_count_5y": 1})})
    view = load_company(con, "ccc")
    assert view.ticker == "CCC"
    assert [(f["code"], f["severity"]) for f in view.flags] == [("payout_fcf_over_100", "red"), ("special_dividend", "info")]


def test_a_not_scored_company_keeps_its_reason_and_has_no_detail(con):
    view = load_company(con, "BANK")
    assert (view.status, view.reason, view.details, view.flags) == ("not_scored", "financial_or_reit", [], [])


def test_an_unknown_ticker_is_none(con):
    assert load_company(con, "ZZZ") is None


def test_annual_data_is_ordered_by_year(con):
    data = load_annual(con, "aaa")
    assert [d["year"] for d in data.dividends] == list(range(2014, 2026))
    assert data.fundamentals[0]["fiscal_year"] == 2015 and data.fundamentals[-1]["fiscal_year"] == 2025
    assert data.year_end_prices[2014] == pytest.approx(20.0)
    assert load_annual(con, "BBB").dividends == []


def test_payments_are_regular_only_and_oldest_first(con):
    payments = load_payments(con, "AAA")
    assert payments[0] == (dt.date(2014, 2, 10), 0.25) and len(payments) == 48   # the special payment is not in the list
    assert payments == sorted(payments)
