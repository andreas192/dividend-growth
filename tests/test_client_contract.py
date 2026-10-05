import httpx
import pytest

from dgi.client.contract import REQUIRED_COLUMNS, check_contract, fetch_contract
from dgi.client.http import ApiClient
from dgi.errors import ContractError

REQUIRED = {"company": {"cik": "BIGINT", "ticker": "VARCHAR"}, "dividend_events": {"ex_date": "DATE"}}


def listing(status="current", sunset=None, versions=("v1",)):
    return {"current": versions[-1], "versions": [{"version": v, "status": status, "sunset": sunset} for v in versions]}


def body(resources):
    return {
        "version": "v1",
        "status": "current",
        "content_hash": "h",
        "resources": [{"name": n, "columns": [{"name": c, "type": t} for c, t in cols.items()]} for n, cols in resources.items()],
    }


GOOD = {"company": {"cik": "BIGINT", "ticker": "VARCHAR", "name": "VARCHAR"}, "dividend_events": {"ex_date": "DATE", "amount": "DOUBLE"}}


def test_a_matching_contract_passes_and_extra_columns_are_fine():
    status = check_contract(listing(), body(GOOD), REQUIRED)
    assert (status.version, status.status, status.content_hash, status.warnings) == ("v1", "current", "h", [])


def test_a_deprecated_version_passes_with_a_warning():
    status = check_contract(listing("deprecated", "2027-01-01"), body(GOOD), REQUIRED)
    assert status.status == "deprecated"
    assert status.warnings == ["contract v1 is deprecated, sunset 2027-01-01"]


def test_a_sunset_version_fails():
    with pytest.raises(ContractError, match="v1 is retired"):
        check_contract(listing("sunset"), body(GOOD), REQUIRED)


def test_a_version_the_api_does_not_offer_fails():
    with pytest.raises(ContractError, match="v1 is not offered.*v2"):
        check_contract(listing(versions=("v2",)), body(GOOD), REQUIRED)


def test_every_problem_is_listed_in_one_error():
    broken = {"company": {"cik": "VARCHAR"}}
    with pytest.raises(ContractError) as exc:
        check_contract(listing(), body(broken), REQUIRED)
    message = str(exc.value)
    assert "company.cik is VARCHAR, expected BIGINT" in message
    assert "company.ticker is missing" in message
    assert "dividend_events is no longer served" in message


def test_the_real_requirements_cover_what_the_pulls_use():
    for resource in ("company", "dividend_events", "split_events", "price_daily", "price_daily_adjusted",
                     "income_statement_latest", "cash_flow_latest", "balance_sheet_latest"):
        assert resource in REQUIRED_COLUMNS
    assert REQUIRED_COLUMNS["income_statement_latest"]["data_flag"] == "VARCHAR"


def test_fetch_contract_reads_both_endpoints():
    def handler(request):
        if request.url.path == "/contracts":
            return httpx.Response(200, json=listing())
        assert request.url.path == "/contracts/v1"
        return httpx.Response(200, json=body(GOOD))

    client = ApiClient("http://api.test", transport=httpx.MockTransport(handler))
    assert fetch_contract(client, REQUIRED).version == "v1"
