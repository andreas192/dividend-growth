import pytest

from dgi.cache import (
    BadQuery, HardFilters, PILLARS, export_rows, parse_query, query_screener, query_unscored, scatter_points, sector_groups, universe_stats,
)
from dgi.cache.screener import SORTS
from tests.cache_fixtures import CFG, scored_cache
from tests.web_fixtures import COMPANIES

WEIGHTS = CFG.pillars


def query(**params):
    return parse_query({k: str(v) for k, v in params.items()}, CFG.hard_filters, WEIGHTS)


@pytest.fixture(scope="module")
def con():
    return scored_cache(COMPANIES)


def tickers(page):
    return [r.ticker for r in page.rows]


# parse_query ---------------------------------------------------------------------------------------------------------

def test_defaults_come_from_the_config():
    q = query()
    assert (q.min_streak, q.max_payout_fcf, q.max_payout_eps, q.min_market_cap, q.min_yield, q.max_yield) == (5, 1.0, None, 1e9, 0.0, None)
    assert q.weights == {"dividend": 30.0, "safety": 30.0, "growth": 20.0, "valuation": 20.0}
    assert (q.sort, q.descending, q.page, q.size, q.show_unscored, q.sector) == ("score", True, 1, 50, False, None)
    assert q.params["max_payout_fcf"] == "100" and q.params["min_cap_bn"] == "1" and q.params["max_payout_eps"] == ""


def test_percent_and_billions_are_converted():
    q = query(max_payout_fcf=80, min_yield=2, max_yield=6.5, min_cap_bn=2.5, max_payout_eps=60)
    assert (q.max_payout_fcf, q.min_yield, q.max_yield, q.min_market_cap, q.max_payout_eps) == (0.8, 0.02, 0.065, 2.5e9, 0.6)


def test_a_blank_nullable_field_means_no_limit_and_a_blank_plain_field_means_default():
    q = query(max_payout_fcf="", max_yield="", min_streak="")
    assert q.max_payout_fcf is None and q.max_yield is None and q.min_streak == 5


def test_the_normalized_params_round_trip():
    q = query(min_streak=9, max_payout_fcf=75, sort="yield", dir="asc", sector="Energy", unscored=1, w_growth=0)
    again = parse_query(q.params, CFG.hard_filters, WEIGHTS)
    assert again == q


def test_sort_defaults_ascending_for_text_columns():
    assert query(sort="ticker").descending is False and query(sort="yield").descending is True


@pytest.mark.parametrize(
    "params, param",
    [
        ({"min_streak": "abc"}, "min_streak"), ({"min_streak": "-1"}, "min_streak"), ({"min_streak": "1.5"}, "min_streak"),
        ({"max_payout_fcf": "x"}, "max_payout_fcf"), ({"min_yield": "101"}, "min_yield"), ({"min_cap_bn": "nan"}, "min_cap_bn"),
        ({"page": "0"}, "page"), ({"page": "x"}, "page"), ({"size": "5"}, "size"), ({"size": "5000"}, "size"),
        ({"sort": "ticker;drop table scores"}, "sort"), ({"sort": "nope"}, "sort"), ({"dir": "sideways"}, "dir"),
        ({"w_safety": "-3"}, "w_safety"), ({"w_dividend": "0", "w_safety": "0", "w_growth": "0", "w_valuation": "0"}, "w_dividend"),
        ({"min_score": "101"}, "min_score"),
    ],
)
def test_bad_input_raises_bad_query_naming_the_parameter(params, param):
    with pytest.raises(BadQuery) as exc:
        query(**params)
    assert exc.value.param == param


# screening -----------------------------------------------------------------------------------------------------------

def test_default_filters_keep_only_the_companies_that_pass_every_limit(con):
    page = query_screener(con, query())
    assert tickers(page) == ["AAA", "BBB"]  # AAA has the longer streak, so it ranks first
    assert page.total == 2 and page.pages == 1 and page.rows[0].position == 1 and page.rows[1].position == 2


def test_each_hard_filter_removes_its_company_and_loosening_it_brings_it_back(con):
    assert "CCC" not in tickers(query_screener(con, query()))
    assert "CCC" in tickers(query_screener(con, query(max_payout_fcf=120)))      # payout 110% allowed
    assert "CCC" in tickers(query_screener(con, query(max_payout_fcf="")))       # no payout limit
    assert "DDD" in tickers(query_screener(con, query(min_streak=0)))
    assert "EEE" in tickers(query_screener(con, query(min_cap_bn=0)))
    assert tickers(query_screener(con, query(min_streak=20))) == ["AAA"]
    assert tickers(query_screener(con, query(min_yield=4))) == ["BBB"]
    assert tickers(query_screener(con, query(max_yield=3))) == ["AAA"]


def test_unknown_values_fail_a_limit_that_is_set():
    con = scored_cache({"AAA": ("2080", {"payout_fcf": None})})
    assert query_screener(con, query(max_payout_fcf=100)).total == 0
    assert query_screener(con, query(max_payout_fcf="")).total == 1


def test_sector_filter_and_list(con):
    assert tickers(query_screener(con, query(sector="Health Care"))) == ["BBB"]
    assert query_screener(con, query(sector="Nowhere")).total == 0
    assert "Consumer Staples" in sector_groups(con) and "Energy" in sector_groups(con)


def test_sorting_ties_break_by_ticker_and_nulls_go_last(con):
    everything = dict(min_streak=0, max_payout_fcf="", min_cap_bn=0)
    assert tickers(query_screener(con, query(sort="ticker", **everything))) == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert tickers(query_screener(con, query(sort="yield", **everything)))[0] == "CCC"
    assert tickers(query_screener(con, query(sort="streak", dir="asc", **everything)))[0] == "DDD"


EVERYTHING = dict(min_streak=0, max_payout_fcf="", min_cap_bn=0)


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_a_null_sort_value_goes_last_in_both_directions_and_equal_values_break_by_ticker(direction):
    con = scored_cache({
        "ZZZ": ("2080", {"div_yield": 0.04}), "AAA": ("2080", {"div_yield": 0.04}), "MMM": ("2080", {"div_yield": 0.02}),
        "NUL": ("2080", {"div_yield": None}),
    })
    rows = query_screener(con, query(sort="yield", dir=direction, **EVERYTHING)).rows
    assert [r.ticker for r in rows][-1] == "NUL" and rows[-1].div_yield is None
    expected = ["MMM", "AAA", "ZZZ"] if direction == "asc" else ["AAA", "ZZZ", "MMM"]
    assert [r.ticker for r in rows][:3] == expected


@pytest.mark.parametrize("sort", sorted(SORTS))
def test_every_sort_key_runs_and_returns_rows(con, sort):
    page = query_screener(con, query(sort=sort, **EVERYTHING))
    assert len(page.rows) == 5


def test_a_hostile_sector_value_is_only_data():
    con = scored_cache(COMPANIES)
    page = query_screener(con, query(sector="x' OR '1'='1"))
    assert page.total == 0 and page.rows == []


def test_paging_reports_total_pages_and_clamps_a_page_past_the_end(con):
    everything = dict(min_streak=0, max_payout_fcf="", min_cap_bn=0, size=10)
    first = query_screener(con, query(**everything))
    assert (first.total, first.pages, len(first.rows)) == (5, 1, 5)
    assert query_screener(con, query(page=9, **everything)).page == 1


def test_a_second_page_holds_the_next_rows_and_a_page_past_the_end_is_the_last_page():
    con = scored_cache({f"T{n:02d}": ("2080", {}) for n in range(25)})
    base = dict(sort="ticker", size=10, **EVERYTHING)
    second = query_screener(con, query(page=2, **base))
    assert (second.total, second.pages, second.page) == (25, 3, 2)
    assert tickers(second) == [f"T{n:02d}" for n in range(10, 20)]
    last = query_screener(con, query(page=99, **base))
    assert last.page == 3 and tickers(last) == [f"T{n:02d}" for n in range(20, 25)]


def test_reweighting_changes_the_score_without_recomputing_bands():
    con = scored_cache({"AAA": ("2080", {}), "BBB": ("2834", {"streak": 6, "payout_fcf": 0.95, "pe": 38.0})})
    all_pillars = {r.ticker: r.score for r in query_screener(con, query()).rows}
    only_dividend = {r.ticker: r.score for r in query_screener(con, query(w_safety=0, w_growth=0, w_valuation=0)).rows}
    stored = dict(con.execute("SELECT ticker, dividend FROM scores").fetchall())
    assert only_dividend == pytest.approx(stored)          # a weighted sum of the stored pillar score
    assert all_pillars != pytest.approx(only_dividend)


def test_a_pillar_a_company_lacks_is_left_out_of_its_weighted_mean():
    con = scored_cache({"AAA": ("2080", {})})
    con.execute("UPDATE scores SET valuation = NULL")
    score = query_screener(con, query()).rows[0].score
    row = con.execute("SELECT dividend, safety, growth FROM scores").fetchone()
    assert score == pytest.approx((row[0] * 30 + row[1] * 30 + row[2] * 20) / 80)


def test_rows_carry_the_history_length_that_the_streak_label_needs(con):
    rows = {r.ticker: r for r in query_screener(con, query()).rows}
    assert (rows["AAA"].streak, rows["AAA"].years_history, rows["BBB"].years_history) == (30, 45, 12)


def test_rows_carry_red_flags(con):
    rows = {r.ticker: r for r in query_screener(con, query(max_payout_fcf="")).rows}
    assert rows["CCC"].red_flags == ["payout_fcf_over_100"] and rows["AAA"].red_flags == []


def test_the_min_score_filter(con):
    best = query_screener(con, query()).rows[0].score
    assert query_screener(con, query(min_score=best + 0.01)).total == 0


def test_universe_stats_and_export_and_scatter(con):
    stats = universe_stats(con, query())
    assert (stats.universe, stats.scored, stats.passing) == (7, 5, 2)
    assert stats.median_yield == pytest.approx((0.025 + 0.045) / 2)
    exported = export_rows(con, query())
    assert [r.ticker for r in exported] == tickers(query_screener(con, query()))
    points = scatter_points(con, query())
    assert {p.ticker for p in points} == {"AAA", "BBB"} and all(p.div_yield and p.dgr_5 for p in points)


def test_unscored_companies_are_listed_with_their_reason(con):
    rows, total = query_unscored(con, query())
    assert total == 2 and {(r.ticker, r.reason) for r in rows} == {("BANK", "financial_or_reit"), ("NEWC", "short_dividend_history")}
    assert query_unscored(con, query(sector="Energy"))[1] == 0


def test_every_pillar_name_is_known_to_the_query_layer():
    assert PILLARS == ("dividend", "safety", "growth", "valuation")
