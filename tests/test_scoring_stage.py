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
