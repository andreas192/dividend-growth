import datetime as dt
import json
import re

import pytest
from starlette.testclient import TestClient

from dgi.cache.build import new_path, swap_in
from dgi.errors import ApiUnavailable
from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG, cache_with, make_meta, persist
from tests.web_fixtures import D, add_history, sample_cache

DAILY = [(D(2025, 1, 1) + dt.timedelta(days=i), 50.0 + i * 0.01) for i in range(400)]


class StubSource:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def daily(self, ticker):
        self.calls.append(ticker)
        if self.error:
            raise self.error
        return DAILY


def app_for(tmp_path, source=None):
    return TestClient(create_app(Settings(data_dir=tmp_path), CFG, source))


@pytest.fixture
def web(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with app_for(tmp_path) as client:
        yield client


def embedded_charts(html: str) -> list[dict]:
    return json.loads(re.search(r'<script type="application/json" id="charts-data">(.*?)</script>', html, re.S).group(1))


def test_a_scored_company_shows_its_header_pillars_tiles_valuation_and_explanation(web):
    response = web.get("/company/AAA")
    text = response.text
    assert response.status_code == 200
    assert "AAA Inc" in text and "Consumer Staples" in text and "$60.00" in text
    assert 'aria-label="Score ' in text and "Score by pillar" in text and "Dividend record" in text and "Data coverage 100%" in text
    assert "Fair value (range)" in text and "Margin of safety" in text
    assert "Fair value per share by required return" in text and "9.0%" in text
    assert "Why this score" in text and "Years of dividend increases" in text and "Payout of free cash flow" in text
    assert "Warning:" not in text


def test_the_page_embeds_the_annual_charts_as_data(web):
    ids = [c["id"] for c in embedded_charts(web.get("/company/AAA").text)]
    assert ids[:3] == ["dps", "yield", "dps_growth"] and {"payout", "per_share", "leverage", "coverage", "shares"} <= set(ids)
    dps = embedded_charts(web.get("/company/AAA").text)[0]
    assert dps["x"][0] == 2014 and dps["series"][0]["values"][0] == 1.0


def test_a_company_with_a_red_flag_shows_it_in_words(web):
    text = web.get("/company/CCC").text
    assert "Warning:" in text and "Dividends paid exceed free cash flow (payout 110%)" in text


def test_a_company_that_is_not_scored_shows_why_and_no_score(web):
    text = web.get("/company/BANK").text
    assert "Not scored" in text and "Bank, insurer or REIT" in text
    assert "Why this score" not in text and "Score by pillar" not in text and "DGI score" not in text
    assert embedded_charts(text) == []


def test_lookup_ignores_case(web):
    assert web.get("/company/aaa").status_code == 200


def test_the_ticker_reaches_the_script_as_json_not_markup(web):
    assert 'window.dgiInitCompany("AAA")' in web.get("/company/AAA").text


# daily drill-down -----------------------------------------------------------------------------------------------------

def test_the_daily_endpoint_returns_price_and_yield_charts(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    source = StubSource()
    with app_for(tmp_path, source) as client:
        body = client.get("/api/company/AAA/daily").json()
    assert body["available"] is True
    assert [c["id"] for c in body["charts"]] == ["price", "yield_daily"]
    assert body["charts"][0]["xType"] == "time" and len(body["charts"][0]["x"]) == 400
    assert source.calls == ["AAA"]


def test_daily_series_are_cached_per_upstream_key_and_reloaded_when_it_changes(tmp_path):
    live = sample_cache(tmp_path / "dgi.duckdb")
    source = StubSource()
    with app_for(tmp_path, source) as client:
        client.get("/api/company/AAA/daily")
        client.get("/api/company/AAA/daily")
        assert source.calls == ["AAA"]
        con = cache_with({"AAA": ("2080", {})})
        add_history(con, "AAA")
        swap_in(persist(con, new_path(live), make_meta(upstream_key="hash-2|2026-10-06T06:00:00+00:00")), live)
        client.get("/api/company/AAA/daily")
    assert source.calls == ["AAA", "AAA"]


def test_large_responses_are_compressed(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with app_for(tmp_path, StubSource()) as client:
        response = client.get("/api/company/AAA/daily", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip" and response.json()["available"] is True


def test_when_the_api_is_down_the_endpoint_says_so_and_the_page_keeps_working(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with app_for(tmp_path, StubSource(ApiUnavailable("cannot reach the investment API"))) as client:
        body = client.get("/api/company/AAA/daily").json()
        assert body["available"] is False and "unavailable" in body["notice"] and "annual figures" in body["notice"]
        assert client.get("/company/AAA").status_code == 200


def test_without_a_price_source_the_endpoint_falls_back(web):
    body = web.get("/api/company/AAA/daily").json()
    assert body["available"] is False and "annual" in body["notice"]
