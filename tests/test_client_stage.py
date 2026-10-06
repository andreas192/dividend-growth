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
