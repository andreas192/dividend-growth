import datetime as dt

import pyarrow as pa
import pytest

from dgi.client.series import ApiPriceSource
from dgi.errors import ApiUnavailable
from tests.fake_api import FakeGold

D = dt.date


def gold():
    return FakeGold({"price_daily_adjusted": pa.table({
        "ticker": ["KO", "KO", "KO", "PG"],
        "trade_date": pa.array([D(2024, 1, 3), D(2024, 1, 2), D(2024, 1, 4), D(2024, 1, 2)], pa.date32()),
        "close": [60.0, 59.0, None, 150.0],
        "split_adjusted_close": [30.0, 29.5, None, 150.0],
    })})


def test_daily_returns_one_tickers_adjusted_closes_oldest_first_and_skips_gaps():
    fake = gold()
    assert ApiPriceSource(fake.client()).daily("KO") == [(D(2024, 1, 2), 29.5), (D(2024, 1, 3), 30.0)]
    assert any("ticker=KO" in r and "price_daily_adjusted" in r for r in fake.requests)


def test_an_unknown_ticker_is_an_empty_series():
    assert ApiPriceSource(gold().client()).daily("ZZZ") == []


def test_an_api_that_is_down_raises_api_unavailable():
    fake = gold()
    fake.down = True
    with pytest.raises(ApiUnavailable):
        ApiPriceSource(fake.client()).daily("KO")
