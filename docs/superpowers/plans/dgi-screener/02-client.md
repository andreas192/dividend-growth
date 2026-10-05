# Phase 2: Investment API client (Tasks 4-6)

Part of `../2026-10-05-dgi-screener.md` (read its Global Constraints and Review Focus first). Spec: `../../specs/2026-10-05-dgi-screener-design.md`, sections "Data flow and cache" and "Errors". Upstream contract: `../investment/docs/superpowers/specs/2026-10-05-gold-http-api-design.md`.

The `client` package is the only HTTP code in the repo. Facts about the API this phase relies on (verified against `../investment/src/invest/api/app.py`):

- `GET /health` -> `{"content_hash", "gold_built_at", "versions": {"v1": "current"}}`. `content_hash` is the hash of the **interface**, not the data; `gold_built_at` is the later of the statement and market builds. Both together are the upstream key.
- `GET /contracts` -> `{"current": "v1", "versions": [{"version", "status", "sunset"}]}`.
- `GET /contracts/v1` -> `{"version", "status", "sunset", "content_hash", "changelog", "resources": [{"name", "columns": [{"name", "type"}], ...}]}`.
- `GET /v1/{resource}?<filters>&format=arrow&limit=&cursor=`: Arrow IPC stream body, next page in the `X-Next-Cursor` header (absent on the last page). Filters: `col=a,b` (IN), `col__gte`, `col__lte` on numeric and date columns. 503 when gold is missing or being swapped (`Retry-After: 1`), 410 when the version is retired, 422 for a bad parameter (`{"error", "param"}`).

---

### Task 4: `ApiClient` (paging, Arrow decode, status mapping)

**Files:**
- Create: `src/dgi/client/__init__.py`, `src/dgi/client/http.py`
- Test: `tests/__init__.py` (empty, makes `tests.arrow_helpers` importable from every test file), `tests/arrow_helpers.py`, `tests/test_client_http.py`

**Interfaces:**
- Consumes: `dgi.errors` (`ApiError`, `ApiUnavailable`, `ContractError`), `dgi.settings.API_VERSION`.
- Produces:
  - `Health(content_hash: str, gold_built_at: str | None, versions: dict[str, str])` with property `upstream_key -> str` (`"<content_hash>|<gold_built_at or ''>"`).
  - `decode_arrow(content: bytes) -> pa.Table` (raises `ApiError`).
  - `raise_for_status(response: httpx.Response) -> None`.
  - `ApiClient(base_url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 120.0, page_limit: int = 100_000)` with `get_json(path, params=None) -> Any`, `health() -> Health`, `pages(resource, filters) -> Iterator[pa.Table]`, `fetch_table(resource, filters) -> pa.Table`, `close()`, and context-manager support.
  - Test helper `tests/arrow_helpers.py: arrow_bytes(table: pa.Table) -> bytes`.

- [ ] **Step 1: Write the failing tests**

```bash
touch tests/__init__.py
```

`tests/arrow_helpers.py`:
```python
import pyarrow as pa


def arrow_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue().to_pybytes()
```

`tests/test_client_http.py` (UNIT: `ApiClient`, `raise_for_status`, `decode_arrow`, `Health`):
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_client_http.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.client'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/client/__init__.py`:
```python
"""The only HTTP code: talks to the investment API (paging, Arrow, contract check, staging)."""
```

`src/dgi/client/http.py`:
```python
from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import httpx
import pyarrow as pa
from pydantic import BaseModel

from dgi.errors import ApiError, ApiUnavailable, ContractError
from dgi.settings import API_VERSION


class Health(BaseModel):
    content_hash: str
    gold_built_at: str | None = None
    versions: dict[str, str] = {}

    @property
    def upstream_key(self) -> str:
        """`content_hash` alone only changes with the schema; the build time changes with every data refresh."""
        return f"{self.content_hash}|{self.gold_built_at or ''}"


def decode_arrow(content: bytes) -> pa.Table:
    try:
        return pa.ipc.open_stream(pa.BufferReader(content)).read_all()
    except (pa.ArrowInvalid, OSError) as exc:
        raise ApiError(f"the API sent an unreadable Arrow stream: {exc}") from exc


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    return str(body.get("error", body) if isinstance(body, dict) else body)[:200]


def raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = _detail(response)
    if response.status_code == 503:
        raise ApiUnavailable(f"the investment API has no usable gold data (503): {detail}")
    if response.status_code == 410:
        raise ContractError(f"contract {API_VERSION} is retired (410): {detail}")
    request = response.request
    raise ApiError(f"{request.method} {request.url.path} answered {response.status_code}: {detail}")


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 120.0,
        page_limit: int = 100_000,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)
        self._page_limit = page_limit

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: Mapping[str, Any] | None) -> httpx.Response:
        try:
            response = self._http.get(path, params=params)
        except httpx.TransportError as exc:
            raise ApiUnavailable(f"cannot reach the investment API at {self._http.base_url}: {exc}") from exc
        raise_for_status(response)
        return response

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        response = self._get(path, params)
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"GET {path} answered 200 but the body is not JSON") from exc

    def health(self) -> Health:
        return Health(**self.get_json("/health"))

    def pages(self, resource: str, filters: Mapping[str, str]) -> Iterator[pa.Table]:
        """One Arrow table per page. An empty resource yields one empty table that keeps its schema."""
        cursor: str | None = None
        while True:
            params = {**filters, "format": "arrow", "limit": self._page_limit}
            if cursor:
                params["cursor"] = cursor
            response = self._get(f"/{API_VERSION}/{resource}", params)
            yield decode_arrow(response.content)
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor:
                return

    def fetch_table(self, resource: str, filters: Mapping[str, str]) -> pa.Table:
        """For small, bounded pulls (one ticker). Bulk pulls stream with `pages`."""
        return pa.concat_tables(list(self.pages(resource, filters)))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_client_http.py`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/client tests/__init__.py tests/arrow_helpers.py tests/test_client_http.py
git commit -m "feat: investment API client with Arrow paging and status mapping"
```

---

### Task 5: Contract dependency check

The refresh needs specific resources and columns. Additive changes pass; a removed resource or column, a retyped column, a retired version or an unoffered version fails with one message listing every problem.

**Files:**
- Create: `src/dgi/client/contract.py`
- Test: `tests/test_client_contract.py`

**Interfaces:**
- Consumes: `ApiClient.get_json`, `dgi.errors.ContractError`, `API_VERSION`.
- Produces:
  - `REQUIRED_COLUMNS: dict[str, dict[str, str]]` resource -> column -> gold type.
  - `ContractStatus(version: str, status: str, content_hash: str, warnings: list[str])`.
  - `check_contract(listing: dict, body: dict, required: Mapping[str, Mapping[str, str]] = REQUIRED_COLUMNS, version: str = API_VERSION) -> ContractStatus`.
  - `fetch_contract(client: ApiClient) -> ContractStatus`.

- [ ] **Step 1: Write the failing tests**

`tests/test_client_contract.py` (UNIT: `check_contract`, `fetch_contract`):
```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_client_contract.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.client.contract'`.

- [ ] **Step 3: Write `src/dgi/client/contract.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_client_contract.py`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/dgi/client/contract.py tests/test_client_contract.py
git commit -m "feat: contract dependency check against the pinned API version"
```

---

### Task 6: Pull specs, staging into DuckDB, and the fake API for tests

`build_pulls` says what to pull and with which filters. `stage_pull` streams pages into a DuckDB staging table so memory stays bounded. `tests/fake_api.py` is a fake investment API (filters, paging, contract, health) used here and by the PIPELINE tests.

**Files:**
- Create: `src/dgi/client/pulls.py`, `src/dgi/client/stage.py`, `tests/fake_api.py`
- Test: `tests/test_client_stage.py`

**Interfaces:**
- Consumes: `ApiClient.pages`.
- Produces:
  - `Pull(table: str, resource: str, filters: dict[str, str])` (frozen dataclass).
  - `build_pulls(today: date) -> list[Pull]` in pull order; staging table names: `stg_company`, `stg_dividends`, `stg_splits`, `stg_income_annual`, `stg_cash_annual`, `stg_balance_annual`, `stg_shares_cover`, `stg_income_quarter`, `stg_cash_quarter`, `stg_prices_latest`, `stg_prices_yearend` (twelve year-end windows append into one table).
  - `table_exists(con, name) -> bool`, `stage_pull(con, pages: Iterable[pa.Table], table: str) -> int` (rows staged; raises `ValueError` for a table name that does not match `stg_[a-z_]+`).
  - `STAGING_TABLES: tuple[str, ...]` every staging table name (used to drop them after stage 1).
  - Test helper `tests/fake_api.py`: `FakeGold(tables: dict[str, pa.Table], *, content_hash="hash-1", built_at="2026-10-04T06:00:00+00:00")` with `.client(page_limit=1000) -> ApiClient`, `.down: bool` (answers 503 when true), `.requests: list[str]` (path?query of every call), `gold_type(arrow_type) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/fake_api.py` (test helper, not a test):
```python
"""A fake investment API: contract, health, filtered and paged Arrow resources. Offline, built in code."""

from __future__ import annotations

import httpx
import pyarrow as pa
import pyarrow.compute as pc

from dgi.client.http import ApiClient
from tests.arrow_helpers import arrow_bytes

RESERVED = {"format", "limit", "cursor"}


def gold_type(t: pa.DataType) -> str:
    if pa.types.is_string(t):
        return "VARCHAR"
    if pa.types.is_int64(t):
        return "BIGINT"
    if pa.types.is_int32(t):
        return "INTEGER"
    if pa.types.is_float64(t):
        return "DOUBLE"
    if pa.types.is_date32(t):
        return "DATE"
    if pa.types.is_boolean(t):
        return "BOOLEAN"
    raise ValueError(f"no gold type for {t}")


def apply_filters(table: pa.Table, params: dict[str, str]) -> pa.Table:
    mask = None
    for key, raw in params.items():
        if key in RESERVED:
            continue
        if key.endswith("__gte"):
            column, op = key[:-5], "gte"
        elif key.endswith("__lte"):
            column, op = key[:-5], "lte"
        else:
            column, op = key, "in"
        col = table[column]
        if op == "in":
            cond = pc.is_in(col, value_set=pa.array(raw.split(","), pa.string()).cast(col.type))
        else:
            bound = pa.scalar(raw).cast(col.type)
            cond = pc.greater_equal(col, bound) if op == "gte" else pc.less_equal(col, bound)
        mask = cond if mask is None else pc.and_(mask, cond)
    return table if mask is None else table.filter(mask)


class FakeGold:
    def __init__(self, tables: dict[str, pa.Table], *, content_hash: str = "hash-1", built_at: str | None = "2026-10-04T06:00:00+00:00") -> None:
        self.tables = tables
        self.content_hash = content_hash
        self.built_at = built_at
        self.down = False
        self.requests: list[str] = []

    def contract_body(self) -> dict:
        resources = [
            {"name": n, "columns": [{"name": f.name, "type": gold_type(f.type)} for f in t.schema]}
            for n, t in self.tables.items()
        ]
        return {"version": "v1", "status": "current", "sunset": None, "content_hash": self.content_hash, "changelog": [], "resources": resources}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.raw_path.decode())
        if self.down:
            return httpx.Response(503, json={"error": "gold.duckdb is missing; run `invest build-gold`"})
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"content_hash": self.content_hash, "gold_built_at": self.built_at, "versions": {"v1": "current"}})
        if path == "/contracts":
            return httpx.Response(200, json={"current": "v1", "versions": [{"version": "v1", "status": "current", "sunset": None}]})
        if path == "/contracts/v1":
            return httpx.Response(200, json=self.contract_body())
        resource = path.removeprefix("/v1/")
        if not path.startswith("/v1/") or resource not in self.tables:
            return httpx.Response(404, json={"error": f"unknown resource: {resource}"})
        params = dict(request.url.params)
        table = apply_filters(self.tables[resource], params)
        offset, limit = int(params.get("cursor", "0")), int(params["limit"])
        headers = {"X-Next-Cursor": str(offset + limit)} if offset + limit < table.num_rows else {}
        return httpx.Response(200, content=arrow_bytes(table.slice(offset, limit)), headers=headers)

    def client(self, page_limit: int = 1000) -> ApiClient:
        return ApiClient("http://api.test", transport=httpx.MockTransport(self.handler), page_limit=page_limit)
```

`tests/test_client_stage.py` (UNIT: `build_pulls`, `stage_pull`, and the fake API's filter behavior the PIPELINE relies on):
```python
import datetime as dt

import duckdb
import pyarrow as pa
import pytest

from dgi.client.pulls import Pull, build_pulls
from dgi.client.stage import STAGING_TABLES, stage_pull, table_exists
from tests.fake_api import FakeGold

TODAY = dt.date(2026, 10, 5)


def by_table(pulls):
    grouped: dict[str, list[Pull]] = {}
    for p in pulls:
        grouped.setdefault(p.table, []).append(p)
    return grouped


def test_build_pulls_covers_every_staging_table_once_and_year_ends_twelve_times():
    grouped = by_table(build_pulls(TODAY))
    assert set(grouped) == set(STAGING_TABLES)
    assert {t: len(p) for t, p in grouped.items() if len(p) > 1} == {"stg_prices_yearend": 12}


def test_statement_pulls_use_the_latest_views_with_narrow_filters():
    grouped = by_table(build_pulls(TODAY))
    income = grouped["stg_income_annual"][0]
    assert income.resource == "income_statement_latest"
    assert income.filters["period_kind"] == "annual"
    assert income.filters["fiscal_year__gte"] == "2014"
    assert "eps_diluted" in income.filters["concept"].split(",")
    balance = grouped["stg_balance_annual"][0]
    assert (balance.resource, balance.filters["period_kind"], balance.filters["fiscal_period"]) == ("balance_sheet_latest", "instant", "FY")
    assert grouped["stg_income_quarter"][0].filters["period_kind"] == "quarter"
    assert grouped["stg_cash_quarter"][0].filters["concept"] == "cash_from_operations,capex"


def test_price_pulls_are_narrow_windows_never_the_full_table():
    grouped = by_table(build_pulls(TODAY))
    assert grouped["stg_prices_latest"][0].filters == {"trade_date__gte": "2026-09-25"}
    windows = [(p.filters["trade_date__gte"], p.filters["trade_date__lte"]) for p in grouped["stg_prices_yearend"]]
    assert windows[0] == ("2014-12-20", "2014-12-31") and windows[-1] == ("2025-12-20", "2025-12-31")
    assert all(p.resource == "price_daily" for p in grouped["stg_prices_latest"] + grouped["stg_prices_yearend"])


def test_stage_pull_creates_then_appends_and_counts_rows():
    con = duckdb.connect()
    first = [pa.table({"ticker": ["A", "B"]}), pa.table({"ticker": ["C"]})]
    assert stage_pull(con, first, "stg_company") == 3
    assert stage_pull(con, [pa.table({"ticker": ["D"]})], "stg_company") == 1
    assert con.execute("SELECT count(*) FROM stg_company").fetchone() == (4,)


def test_stage_pull_keeps_the_schema_of_an_empty_resource():
    con = duckdb.connect()
    assert stage_pull(con, [pa.table({"ticker": pa.array([], pa.string())})], "stg_splits") == 0
    assert table_exists(con, "stg_splits")
    assert con.execute("SELECT count(*) FROM stg_splits").fetchone() == (0,)


@pytest.mark.parametrize("name", ["company; DROP TABLE x", "stg_Company", "dividend_annual", "stg_"])
def test_stage_pull_rejects_names_that_are_not_staging_tables(name):
    with pytest.raises(ValueError, match="not a staging table"):
        stage_pull(duckdb.connect(), [pa.table({"a": [1]})], name)


def test_table_exists():
    con = duckdb.connect()
    con.execute("CREATE TABLE stg_x AS SELECT 1 AS a")
    assert table_exists(con, "stg_x") and not table_exists(con, "stg_y")


def test_the_fake_api_filters_and_pages_like_the_real_one():
    gold = FakeGold({"dividend_events": pa.table({
        "ticker": ["KO", "KO", "PG"],
        "ex_date": pa.array([dt.date(2024, 3, 14), dt.date(2025, 3, 14), dt.date(2025, 1, 20)], pa.date32()),
        "amount": [0.485, 0.51, 1.0065],
    })})
    got = gold.client(page_limit=1).fetch_table("dividend_events", {"ticker": "KO", "ex_date__gte": "2025-01-01"})
    assert got.column("amount").to_pylist() == [0.51]
    both = gold.client().fetch_table("dividend_events", {"ticker": "KO,PG"})
    assert both.num_rows == 3
    assert any("ticker=KO%2CPG" in r or "ticker=KO,PG" in r for r in gold.requests)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_client_stage.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'dgi.client.pulls'`.

- [ ] **Step 3: Write the implementation**

`src/dgi/client/pulls.py`:
```python
"""What the refresh pulls from the API, as data: one Pull per request family."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

STATEMENT_YEARS = 12          # fiscal years back from the current year (11 full years plus a margin)
PRICE_YEAREND_YEARS = 12      # December windows back from last year
QUARTER_DAYS = 800            # recent quarters for the TTM basis
SHARES_DAYS = 730             # cover-page share counts

INCOME_ANNUAL = "revenue,net_income,operating_income,eps_diluted,interest_expense,shares_diluted_weighted"
CASH_ANNUAL = "cash_from_operations,capex,dividends_paid,depreciation_amortization"
BALANCE_ANNUAL = (
    "total_equity,current_assets,current_liabilities,cash_and_equivalents,short_term_investments,"
    "long_term_debt,short_term_debt,current_portion_long_term_debt"
)
SHARES = "shares_outstanding_cover,shares_outstanding"


@dataclass(frozen=True)
class Pull:
    table: str
    resource: str
    filters: dict[str, str]


def build_pulls(today: date) -> list[Pull]:
    first_fy = str(today.year - STATEMENT_YEARS)
    quarters_from = (today - timedelta(days=QUARTER_DAYS)).isoformat()
    shares_from = (today - timedelta(days=SHARES_DAYS)).isoformat()
    pulls = [
        Pull("stg_company", "company", {}),
        Pull("stg_dividends", "dividend_events", {}),
        Pull("stg_splits", "split_events", {}),
        Pull("stg_income_annual", "income_statement_latest", {"period_kind": "annual", "concept": INCOME_ANNUAL, "fiscal_year__gte": first_fy}),
        Pull("stg_cash_annual", "cash_flow_latest", {"period_kind": "annual", "concept": CASH_ANNUAL, "fiscal_year__gte": first_fy}),
        Pull("stg_balance_annual", "balance_sheet_latest", {"period_kind": "instant", "fiscal_period": "FY", "concept": BALANCE_ANNUAL, "fiscal_year__gte": first_fy}),
        Pull("stg_shares_cover", "balance_sheet_latest", {"period_kind": "instant", "concept": SHARES, "period_end__gte": shares_from}),
        Pull("stg_income_quarter", "income_statement_latest", {"period_kind": "quarter", "concept": "net_income,shares_diluted_weighted", "period_end__gte": quarters_from}),
        Pull("stg_cash_quarter", "cash_flow_latest", {"period_kind": "quarter", "concept": "cash_from_operations,capex", "period_end__gte": quarters_from}),
        Pull("stg_prices_latest", "price_daily", {"trade_date__gte": (today - timedelta(days=10)).isoformat()}),
    ]
    for year in range(today.year - PRICE_YEAREND_YEARS, today.year):
        pulls.append(Pull("stg_prices_yearend", "price_daily", {"trade_date__gte": f"{year}-12-20", "trade_date__lte": f"{year}-12-31"}))
    return pulls
```

`src/dgi/client/stage.py`:
```python
"""Stream API pages into DuckDB staging tables so memory stays bounded."""

from __future__ import annotations

import re
from collections.abc import Iterable

import duckdb
import pyarrow as pa

STAGING_TABLES: tuple[str, ...] = (
    "stg_company", "stg_dividends", "stg_splits", "stg_income_annual", "stg_cash_annual", "stg_balance_annual",
    "stg_shares_cover", "stg_income_quarter", "stg_cash_quarter", "stg_prices_latest", "stg_prices_yearend",
)
_STAGING_NAME = re.compile(r"^stg_[a-z_]+$")


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()
    return row[0] > 0


def stage_pull(con: duckdb.DuckDBPyConnection, pages: Iterable[pa.Table], table: str) -> int:
    """Create `table` from the first page and append the rest; returns the number of rows staged."""
    if not _STAGING_NAME.match(table):
        raise ValueError(f"{table!r} is not a staging table name")
    rows = 0
    for page in pages:
        con.register("_stage_page", page)
        try:
            if table_exists(con, table):
                con.execute(f"INSERT INTO {table} SELECT * FROM _stage_page")
            else:
                con.execute(f"CREATE TABLE {table} AS SELECT * FROM _stage_page")
        finally:
            con.unregister("_stage_page")
        rows += page.num_rows
    return rows
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_client_stage.py`
Expected: PASS (11 passed). The fake-API test pins that `FakeGold` filters the way the real API does; if `pa.scalar(raw).cast(col.type)` fails for dates on this pyarrow version, change the bound to `pa.array([raw]).cast(col.type)[0]` in `tests/fake_api.py`.

- [ ] **Step 5: Run the whole suite and commit**

```bash
uv run pytest
git add src/dgi/client tests/fake_api.py tests/test_client_stage.py
git commit -m "feat: pull specs, DuckDB staging and a fake investment API for tests"
```
Expected: all tests pass.
