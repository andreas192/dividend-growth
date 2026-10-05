import datetime as dt

import duckdb
import pytest

from dgi import schema


def test_every_table_has_columns_and_ddl():
    assert set(schema.PERSISTED_TABLES) == set(schema.COLUMN_DEFS)
    for name in schema.COLUMN_DEFS:
        assert schema.columns(name)
        assert schema.ddl(name).startswith(f"CREATE TABLE {name} (")


def test_create_tables_matches_declared_columns():
    con = duckdb.connect()
    schema.create_tables(con)
    for name in schema.COLUMN_DEFS:
        assert schema.table_columns(con, name) == schema.columns(name)


def test_recreate_tables_replaces_an_existing_table_and_creates_a_missing_one():
    con = duckdb.connect()
    schema.create_tables(con, ["flags"])
    con.execute("INSERT INTO flags VALUES ('KO', 'x', 'red', 'text')")
    schema.recreate_tables(con, ["flags", "scores"])
    assert con.execute("SELECT count(*) FROM flags").fetchone() == (0,)
    assert schema.table_columns(con, "scores") == schema.columns("scores")


def test_create_tables_can_create_a_subset():
    con = duckdb.connect()
    schema.create_tables(con, ["scores"])
    assert schema.table_columns(con, "scores")
    assert schema.table_columns(con, "flags") == []


def test_insert_rows_fills_named_columns_and_leaves_the_rest_null():
    con = duckdb.connect()
    schema.create_tables(con, ["dividend_annual"])
    n = schema.insert_rows(con, "dividend_annual", [{"ticker": "KO", "year": 2024, "dps": 1.94, "complete": True}])
    assert n == 1
    assert con.execute("SELECT ticker, year, dps, n_payments, complete FROM dividend_annual").fetchall() == [
        ("KO", 2024, 1.94, None, True)
    ]


def test_insert_rows_converts_dates_and_accepts_no_rows():
    con = duckdb.connect()
    schema.create_tables(con, ["dividend_payment"])
    schema.insert_rows(con, "dividend_payment", [{"ticker": "KO", "ex_date": dt.date(2024, 3, 14), "year": 2024, "amount_adj": 0.485, "is_special": False}])
    assert schema.insert_rows(con, "dividend_payment", []) == 0
    assert con.execute("SELECT count(*) FROM dividend_payment").fetchone() == (1,)


def test_insert_rows_rejects_an_unknown_table():
    with pytest.raises(KeyError):
        schema.insert_rows(duckdb.connect(), "nope", [])
