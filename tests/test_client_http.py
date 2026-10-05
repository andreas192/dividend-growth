import httpx
import pyarrow as pa
import pytest

from dgi.client.http import ApiClient, Health, decode_arrow
from dgi.errors import ApiError, ApiUnavailable, ContractError
from tests.arrow_helpers import arrow_bytes


def make_client(handler, page_limit=2) -> ApiClient:
    return ApiClient("http://api.test", transport=httpx.MockTransport(handler), page_limit=page_limit)


def paged_handler(table: pa.Table, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        offset = int(request.url.params.get("cursor", "0"))
        limit = int(request.url.params["limit"])
        headers = {"X-Next-Cursor": str(offset + limit)} if offset + limit < table.num_rows else {}
        return httpx.Response(200, content=arrow_bytes(table.slice(offset, limit)), headers=headers)

    return handler


def test_pages_follow_the_cursor_until_the_header_is_absent():
    table = pa.table({"ticker": ["A", "B", "C", "D", "E"]})
    seen: list = []
    pages = list(make_client(paged_handler(table, seen)).pages("company", {"ticker": "A,B"}))
    assert [p.num_rows for p in pages] == [2, 2, 1]
    assert seen[0] == ("/v1/company", {"ticker": "A,B", "format": "arrow", "limit": "2"})
    assert seen[1][1]["cursor"] == "2"
    assert seen[2][1]["cursor"] == "4"


def test_an_empty_resource_yields_one_empty_table_that_keeps_its_schema():
    table = pa.table({"ticker": pa.array([], pa.string())})
    pages = list(make_client(paged_handler(table, [])).pages("company", {}))
    assert len(pages) == 1 and pages[0].num_rows == 0 and pages[0].schema.names == ["ticker"]


def test_fetch_table_concatenates_all_pages():
    table = pa.table({"n": [1, 2, 3, 4, 5]})
    got = make_client(paged_handler(table, [])).fetch_table("price_daily", {"ticker": "KO"})
    assert got.column("n").to_pylist() == [1, 2, 3, 4, 5]


def test_503_is_api_unavailable():
    client = make_client(lambda r: httpx.Response(503, json={"error": "gold.duckdb is missing"}))
    with pytest.raises(ApiUnavailable, match="gold.duckdb is missing"):
        list(client.pages("company", {}))


def test_a_connection_failure_is_api_unavailable():
    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiUnavailable, match="cannot reach the investment API"):
        make_client(refuse).get_json("/health")


def test_410_is_a_contract_error():
    client = make_client(lambda r: httpx.Response(410, json={"error": "v1 is retired", "current": "v2"}))
    with pytest.raises(ContractError, match="retired"):
        list(client.pages("company", {}))


def test_422_is_an_api_error_that_names_the_problem():
    client = make_client(lambda r: httpx.Response(422, json={"error": "unknown column", "param": "tickr"}))
    with pytest.raises(ApiError, match="unknown column"):
        list(client.pages("company", {"tickr": "A"}))


def test_a_body_that_is_not_arrow_is_an_api_error():
    client = make_client(lambda r: httpx.Response(200, content=b"not arrow"))
    with pytest.raises(ApiError, match="unreadable Arrow"):
        list(client.pages("company", {}))


def test_decode_arrow_round_trips():
    table = pa.table({"a": [1, 2]})
    assert decode_arrow(arrow_bytes(table)).equals(table)


def test_health_builds_the_upstream_key():
    body = {"content_hash": "abc", "gold_built_at": "2026-10-04T06:00:00+00:00", "versions": {"v1": "current"}}
    health = make_client(lambda r: httpx.Response(200, json=body)).health()
    assert health == Health(content_hash="abc", gold_built_at="2026-10-04T06:00:00+00:00", versions={"v1": "current"})
    assert health.upstream_key == "abc|2026-10-04T06:00:00+00:00"


def test_health_without_a_build_time_still_has_a_key():
    health = make_client(lambda r: httpx.Response(200, json={"content_hash": "abc", "gold_built_at": None, "versions": {}})).health()
    assert health.upstream_key == "abc|"


def test_get_json_rejects_a_body_that_is_not_json():
    with pytest.raises(ApiError, match="not JSON"):
        make_client(lambda r: httpx.Response(200, content=b"<html>")).get_json("/health")
