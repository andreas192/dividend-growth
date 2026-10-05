import json

import pytest
from starlette.testclient import TestClient

from dgi.cache.build import new_path, swap_in
from dgi.settings import Settings
from dgi.web.app import create_app
from tests.cache_fixtures import CFG, cache_with, make_meta, persist
from tests.web_fixtures import sample_cache


def client_for(tmp_path, price_source=None) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path), CFG, price_source))


@pytest.fixture
def web(tmp_path):
    sample_cache(tmp_path / "dgi.duckdb")
    with client_for(tmp_path) as client:
        yield client


def test_without_a_cache_pages_say_to_run_refresh_and_health_still_answers(tmp_path):
    with client_for(tmp_path) as client:
        for path in ("/", "/company/AAA", "/screener.csv"):
            response = client.get(path)
            assert response.status_code == 503 and "dgi refresh" in response.text
        assert client.get("/health").json() == {"status": "no_cache"}
        assert client.get("/api/scatter").status_code == 503
        assert client.get("/api/company/AAA/daily").status_code == 503


def test_health_reports_the_cache(web):
    body = web.get("/health").json()
    assert body["status"] == "ok" and body["upstream_key"] == make_meta().upstream_key and body["contract_version"] == "v1"
    assert body["counts"]["company_dim"] == 7 and body["scored"] == 5
    assert body["not_scored"] == {"financial_or_reit": 1, "short_dividend_history": 1}


def test_methodology_shows_the_active_configuration_with_or_without_a_cache(web, tmp_path):
    text = web.get("/methodology").text
    assert "Pillar weights" in text and "Dividend record" in text and "30%" in text
    assert "Years of dividend increases" in text and "6798" in text and "Gordon growth" in text
    with client_for(tmp_path / "nowhere") as bare:
        assert bare.get("/methodology").status_code == 200


def test_static_assets_including_the_vendored_charting_library_are_served(web):
    for path in ("/static/app.css", "/static/charts.js", "/static/vendor/echarts.min.js"):
        assert web.get(path).status_code == 200, path
    assert len(web.get("/static/vendor/echarts.min.js").content) > 500_000


def test_unknown_addresses_and_malformed_tickers_are_a_404_page_not_an_error(web):
    for path in ("/nope", "/company/ZZZ", "/company/bad;ticker", "/company/AA%2FA", "/company/" + "A" * 11, "/api/company/ZZZ/daily"):
        response = web.get(path)
        assert response.status_code == 404, path
    assert "nothing at this address" in web.get("/nope").text


def test_the_footer_shows_the_build_and_any_contract_warning(tmp_path):
    persist(cache_with({"AAA": ("2080", {})}), tmp_path / "dgi.duckdb", make_meta(contract_warnings="contract v1 is deprecated, sunset 2027-01-01"))
    with client_for(tmp_path) as client:
        text = client.get("/methodology").text
    assert "Cache built 2026-10-05 07:00 UTC" in text and "contract v1" in text and "deprecated, sunset 2027-01-01" in text


def test_a_cache_swapped_under_the_running_app_is_served_without_a_restart(tmp_path):
    live = persist(cache_with({"OLD": ("2080", {})}), tmp_path / "dgi.duckdb")
    with client_for(tmp_path) as client:
        assert client.get("/health").json()["counts"]["company_dim"] == 1
        swap_in(persist(cache_with({"A": ("2080", {}), "B": ("2080", {}), "C": ("2080", {})}), new_path(live)), live)
        assert client.get("/health").json()["counts"]["company_dim"] == 3
