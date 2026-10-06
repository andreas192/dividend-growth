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
