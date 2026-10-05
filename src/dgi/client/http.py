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
