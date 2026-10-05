"""One ticker's daily prices from the API, for the company page's daily charts."""

from __future__ import annotations

from datetime import date

from dgi.client.http import ApiClient


class ApiPriceSource:
    """Split-adjusted daily closes of one ticker. Raises the client's errors when the API cannot answer."""

    def __init__(self, client: ApiClient) -> None:
        self._client = client

    def daily(self, ticker: str) -> list[tuple[date, float]]:
        table = self._client.fetch_table("price_daily_adjusted", {"ticker": ticker})
        rows = zip(table["trade_date"].to_pylist(), table["split_adjusted_close"].to_pylist())
        return sorted((d, close) for d, close in rows if d is not None and close is not None)
