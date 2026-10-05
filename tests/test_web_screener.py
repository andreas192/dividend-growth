import csv
import io
import re

import pytest
from starlette.testclient import TestClient

from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG, make_meta, persist, scored_cache
from tests.web_fixtures import COMPANIES, sample_cache


@pytest.fixture
def web(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        yield client


def rows(html: str) -> list[str]:
    """Tickers linked in the ranked table, in order."""
    import re
    return re.findall(r'<td><a href="/company/([A-Z.\-]+)">', html)


def test_the_default_screen_lists_the_companies_that_pass_the_config_filters(web):
    response = web.get("/")
    assert response.status_code == 200
    assert rows(response.text) == ["AAA", "BBB"]
    assert "7</span><span class=\"label\">companies listed" in response.text
    assert "Ranked companies (2)" in response.text and "Export CSV" in response.text


def test_a_streak_that_reaches_the_start_of_the_history_gets_a_plus(web):
    text = web.get("/").text
    assert re.search(r'<td class="num">12\+</td>', text) and re.search(r'<td class="num">30</td>', text)   # BBB: a 12-year streak on 12 years of history; AAA: 30 of 45
    assert "A plus after a streak" in text
    assert re.search(r'<span class="value">12\+</span>', web.get("/company/BBB").text)


def test_the_filter_bar_is_filled_from_the_url_and_the_config_defaults(web):
    text = web.get("/?min_streak=9&sector=Health+Care&w_growth=0").text
    assert 'name="min_streak" min="0" step="1" value="9"' in text
    assert '<option value="Health Care" selected>' in text
    assert 'name="w_growth" min="0" max="100" step="5" value="0"' in text
    assert rows(text) == ["BBB"]


def test_loosening_a_filter_adds_a_company_and_a_warning_badge_shows(web):
    text = web.get("/?max_payout_fcf=").text
    assert rows(text) == ["AAA", "BBB", "CCC"]
    assert "▲ 1 warning" in text


@pytest.mark.parametrize("query, param", [("min_streak=abc", "min_streak"), ("page=0", "page"), ("sort=ticker;drop+table+scores", "sort"), ("dir=up", "dir"), ("w_dividend=0&w_safety=0&w_growth=0&w_valuation=0", "w_dividend")])
def test_bad_parameters_answer_422_naming_the_parameter(web, query, param):
    response = web.get("/?" + query)
    assert response.status_code == 422 and param in response.text
    assert web.get("/screener.csv?" + query).status_code == 422
    assert web.get("/api/scatter?" + query).status_code == 422


def test_sort_links_flip_direction_and_keep_the_filters(web):
    text = web.get("/?sort=yield&dir=desc&min_streak=3").text
    assert 'aria-sort="descending"' in text
    assert "sort=yield&amp;dir=asc" in text and "min_streak=3" in text


def test_paging_links_appear_only_when_there_is_more_than_one_page(web):
    assert "Next" not in web.get("/").text
    text = web.get("/?min_streak=0&max_payout_fcf=&min_cap_bn=0&size=10").text
    assert "Next" not in text      # five companies fit on one page of ten
    assert "Page " not in text


def test_not_scored_companies_are_listed_with_their_reason(web):
    text = web.get("/?unscored=1").text
    assert "Not scored (2)" in text and "BANK" in text and "NEWC" in text
    assert "Bank, insurer or REIT" in text and "Fewer complete years" in text
    assert "Export CSV" not in text


def test_csv_export_matches_the_table_and_neutralizes_formulas(tmp_path):
    con = scored_cache(COMPANIES)
    con.execute("UPDATE company_dim SET name = '=HYPERLINK(\"http://evil\")' WHERE ticker = 'AAA'")
    persist(con, tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        response = client.get("/screener.csv?min_streak=0&max_payout_fcf=&min_cap_bn=0")
    assert response.headers["content-type"].startswith("text/csv") and "attachment" in response.headers["content-disposition"]
    table = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["ticker"] for r in table] == ["AAA", "EEE", "BBB", "DDD", "CCC"]   # best score first
    aaa = next(r for r in table if r["ticker"] == "AAA")
    assert aaa["name"].startswith("'=") and aaa["yield_pct"] == "2.5" and aaa["streak"] == "30"
    ccc = next(r for r in table if r["ticker"] == "CCC")
    assert ccc["red_flags"] == "payout_fcf_over_100"


def test_company_names_are_escaped_in_the_page(tmp_path):
    con = scored_cache(COMPANIES)
    con.execute("UPDATE company_dim SET name = '<script>alert(1)</script>' WHERE ticker = 'AAA'")
    persist(con, tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        text = client.get("/").text
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_scatter_api_returns_percent_points_for_the_filtered_set(web):
    body = web.get("/api/scatter").json()
    points = {p["ticker"]: p for p in body["points"]}
    assert set(points) == {"AAA", "BBB"}
    assert points["AAA"]["yield"] == 2.5 and points["AAA"]["dgr5"] == 7.0 and points["AAA"]["score"] > 0
    assert set(web.get("/api/scatter?min_streak=0&max_payout_fcf=&min_cap_bn=0").json()["points"][0]) == {"ticker", "name", "yield", "dgr5", "score"}


def many(n, prefix, overrides):
    return {f"{prefix}{i:02d}": ("2080", dict(overrides, market_cap=5e9)) for i in range(n)}


def links(html: str) -> list[str]:
    return re.findall(r'<td><a href="/company/([A-Z0-9.\-]+)">', html)


def test_the_ranked_table_pages_with_a_next_link_when_there_are_more_rows_than_the_page_size(tmp_path):
    persist(scored_cache(many(12, "R", {"streak": 20})), tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        first = client.get("/?size=10").text
        second = client.get("/?size=10&page=2").text
    assert len(links(first)) == 10 and "Page 1 of 2" in first and "page=2" in first and "Next" in first and "Previous" not in first
    assert len(links(second)) == 2 and "Page 2 of 2" in second and "Previous" in second and "Next" not in second


def test_the_not_scored_list_is_paged_and_a_page_past_the_end_clamps(tmp_path):
    persist(scored_cache(many(12, "N", {"years_history": 3})), tmp_path / "dgi.duckdb")
    with TestClient(create_app(Settings(data_dir=tmp_path), CFG)) as client:
        first = client.get("/?unscored=1&size=10").text
        second = client.get("/?unscored=1&size=10&page=2").text
        past = client.get("/?unscored=1&size=10&page=9").text
    assert "Not scored (12)" in first and len(links(first)) == 10
    assert "Page 1 of 2" in first and "Next" in first and "unscored=1" in first and "page=2" in first
    assert len(links(second)) == 2 and "Page 2 of 2" in second and "Previous" in second
    assert len(links(past)) == 2 and "Page 2 of 2" in past
