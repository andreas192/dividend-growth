"""Check that the API still serves the resources and columns the refresh depends on."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from dgi.client.http import ApiClient
from dgi.errors import ContractError
from dgi.settings import API_VERSION

_STATEMENT = {
    "ticker": "VARCHAR", "cik": "BIGINT", "concept": "VARCHAR", "period_kind": "VARCHAR", "period_end": "DATE",
    "fiscal_year": "INTEGER", "fiscal_period": "VARCHAR", "value": "DOUBLE", "filed": "DATE", "data_flag": "VARCHAR",
}

REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "company": {"cik": "BIGINT", "name": "VARCHAR", "ticker": "VARCHAR", "sic": "VARCHAR", "sic_description": "VARCHAR"},
    "dividend_events": {"ticker": "VARCHAR", "ex_date": "DATE", "amount": "DOUBLE"},
    "split_events": {"ticker": "VARCHAR", "ex_date": "DATE", "ratio": "DOUBLE"},
    "price_daily": {"ticker": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE"},
    "price_daily_adjusted": {
        "ticker": "VARCHAR", "trade_date": "DATE", "close": "DOUBLE", "split_adjusted_close": "DOUBLE",
    },
    "income_statement_latest": _STATEMENT,
    "cash_flow_latest": _STATEMENT,
    "balance_sheet_latest": _STATEMENT,
}


class ContractStatus(BaseModel):
    version: str
    status: str
    content_hash: str
    warnings: list[str] = []


def _offered(listing: Mapping, version: str) -> dict:
    versions = listing.get("versions", [])
    for entry in versions:
        if entry["version"] == version:
            return entry
    offered = ", ".join(v["version"] for v in versions) or "none"
    raise ContractError(f"contract {version} is not offered; the API offers: {offered}")


def _problems(body: Mapping, required: Mapping[str, Mapping[str, str]], version: str) -> list[str]:
    served = {r["name"]: {c["name"]: c["type"] for c in r["columns"]} for r in body.get("resources", [])}
    problems: list[str] = []
    for resource, columns in required.items():
        if resource not in served:
            problems.append(f"{resource} is no longer served by {version}")
            continue
        for column, expected in columns.items():
            actual = served[resource].get(column)
            if actual is None:
                problems.append(f"{resource}.{column} is missing")
            elif actual != expected:
                problems.append(f"{resource}.{column} is {actual}, expected {expected}")
    return problems


def check_contract(
    listing: Mapping,
    body: Mapping,
    required: Mapping[str, Mapping[str, str]] = REQUIRED_COLUMNS,
    version: str = API_VERSION,
) -> ContractStatus:
    entry = _offered(listing, version)
    if entry["status"] == "sunset":
        raise ContractError(f"contract {version} is retired (sunset); the API's current version is {listing.get('current')}")
    problems = _problems(body, required, version)
    if problems:
        raise ContractError(f"contract {version} no longer matches what dgi needs: " + "; ".join(problems))
    warnings = []
    if entry["status"] == "deprecated":
        warnings.append(f"contract {version} is deprecated, sunset {entry.get('sunset')}")
    return ContractStatus(version=version, status=entry["status"], content_hash=body["content_hash"], warnings=warnings)


def fetch_contract(client: ApiClient, required: Mapping[str, Mapping[str, str]] = REQUIRED_COLUMNS) -> ContractStatus:
    listing = client.get_json("/contracts")
    body = client.get_json(f"/contracts/{API_VERSION}")
    return check_contract(listing, body, required)
