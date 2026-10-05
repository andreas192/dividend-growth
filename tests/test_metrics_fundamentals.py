import pytest

from dgi import schema
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import D, add_facts, add_split, metrics_con


def build(con):
    run_sql_file(con, "fundamentals_annual")


def row(con, ticker="AAA", fy=2024):
    cur = con.execute("SELECT * FROM fundamentals_annual WHERE ticker = ? AND fiscal_year = ?", [ticker, fy])
    names = [d[0] for d in cur.description]
    values = cur.fetchone()
    return dict(zip(names, values)) if values else None


def test_statements_are_pivoted_and_ratios_derived():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("revenue", 2024, 1000.0), ("net_income", 2024, 100.0), ("operating_income", 2024, 150.0), ("interest_expense", 2024, 10.0), ("shares_diluted_weighted", 2024, 50.0), ("eps_diluted", 2024, 2.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("cash_from_operations", 2024, 140.0), ("capex", 2024, -40.0), ("dividends_paid", 2024, -60.0), ("depreciation_amortization", 2024, 30.0)])
    add_facts(con, "stg_balance_annual", "AAA", [("total_equity", 2024, 400.0), ("current_assets", 2024, 300.0), ("current_liabilities", 2024, 150.0), ("cash_and_equivalents", 2024, 50.0), ("short_term_investments", 2024, 10.0), ("long_term_debt", 2024, 200.0), ("short_term_debt", 2024, 20.0), ("current_portion_long_term_debt", 2024, 30.0)])
    build(con)
    r = row(con)
    assert (r["revenue"], r["net_income"], r["capex"], r["dividends_paid"]) == (1000.0, 100.0, 40.0, 60.0)
    assert r["fcf"] == 100.0 and r["fcf_per_share"] == 2.0
    assert r["debt"] == 250.0 and r["net_debt"] == 190.0 and r["ebitda"] == 180.0
    assert r["op_margin"] == pytest.approx(0.15)
    assert r["payout_earnings"] == pytest.approx(0.6) and r["payout_fcf"] == pytest.approx(0.6)
    assert r["period_end"] == D(2024, 12, 31)


def test_flagged_rows_are_ignored():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)])
    add_facts(con, "stg_income_annual", "AAA", [("revenue", 2024, 9e9)], data_flag="period_end_after_filed")
    build(con)
    assert row(con)["revenue"] is None and row(con)["net_income"] == 100.0


def test_the_latest_filed_value_wins_for_one_concept_and_year():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)], filed_lag_days=60)
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 90.0)], filed_lag_days=400)  # a later restatement
    build(con)
    assert row(con)["net_income"] == 90.0


def test_rows_without_a_ticker_are_dropped():
    con = metrics_con()
    con.execute("INSERT INTO stg_income_annual VALUES (NULL, 9, 'net_income', 'annual', DATE '2024-12-31', 2024, 'FY', 5.0, DATE '2025-02-20', NULL)")
    build(con)
    assert con.execute("SELECT count(*) FROM fundamentals_annual").fetchone() == (0,)


def test_eps_and_shares_are_restated_for_splits_after_the_filing_only():
    con = metrics_con()
    # FY2021 filed 2022-03-01. A 2-for-1 split on 2022-06-01 is after the filing: restate. A split on 2021-06-01 is before: already in the filing.
    add_facts(con, "stg_income_annual", "AAA", [("eps_diluted", 2021, 4.0), ("shares_diluted_weighted", 2021, 50.0)], filed_lag_days=60)
    add_split(con, "AAA", D(2022, 6, 1), 2.0)
    add_split(con, "AAA", D(2021, 6, 1), 3.0)
    build(con)
    r = row(con, fy=2021)
    assert r["eps_diluted"] == pytest.approx(2.0) and r["shares_diluted"] == pytest.approx(100.0)


def test_payout_is_capped_not_null_when_earnings_are_not_positive_and_dividends_are_paid():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, -50.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("cash_from_operations", 2024, 10.0), ("capex", 2024, 30.0), ("dividends_paid", 2024, -20.0)])
    build(con)
    r = row(con)
    assert r["payout_earnings"] == 9.99 and r["payout_fcf"] == 9.99 and r["fcf"] == -20.0


def test_payout_is_zero_without_dividends_and_null_when_dividends_are_unknown():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("dividends_paid", 2024, 0.0)])
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2024, 100.0)], cik=2)
    build(con)
    assert row(con, "AAA")["payout_earnings"] == 0.0
    assert row(con, "BBB")["payout_earnings"] is None


def test_a_balance_sheet_without_debt_tags_means_no_debt_but_no_balance_sheet_means_unknown():
    con = metrics_con()
    add_facts(con, "stg_balance_annual", "AAA", [("total_equity", 2024, 100.0), ("cash_and_equivalents", 2024, 40.0)])
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2024, 1.0)], cik=2)
    build(con)
    assert row(con, "AAA")["debt"] == 0.0 and row(con, "AAA")["net_debt"] == -40.0
    assert row(con, "BBB")["debt"] is None and row(con, "BBB")["net_debt"] is None


def test_fcf_is_unknown_when_capex_is_missing():
    con = metrics_con()
    add_facts(con, "stg_cash_annual", "AAA", [("cash_from_operations", 2024, 100.0)])
    build(con)
    assert row(con)["fcf"] is None


def test_each_fiscal_year_is_its_own_row():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2023, 80.0), ("net_income", 2024, 100.0)])
    build(con)
    assert con.execute("SELECT fiscal_year, net_income FROM fundamentals_annual ORDER BY 1").fetchall() == [(2023, 80.0), (2024, 100.0)]


def add_regular_dividends(con, ticker, year, dps, complete=True):
    schema.insert_rows(con, "dividend_annual", [{"ticker": ticker, "year": year, "dps": dps, "n_payments": 4, "special_total": 0.0, "complete": complete}])


def test_reported_dividends_paid_win_over_the_per_share_estimate():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("dividends_paid", 2024, -60.0)])
    add_regular_dividends(con, "AAA", 2024, 9.0)
    build(con)
    r = row(con)
    assert r["dividends_paid"] == 60.0 and r["dividends_estimated"] is False and r["payout_earnings"] == pytest.approx(0.6)


def test_missing_dividends_paid_is_estimated_from_dividend_per_share_and_shares():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)])
    add_regular_dividends(con, "AAA", 2024, 1.2)
    build(con)
    r = row(con)
    assert r["dividends_paid"] == pytest.approx(60.0) and r["dividends_estimated"] is True and r["payout_earnings"] == pytest.approx(0.6)


def test_no_estimate_without_shares_without_dividends_or_from_an_incomplete_year():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2024, 100.0)])                                      # no shares
    add_regular_dividends(con, "AAA", 2024, 1.2)
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)], cik=2)  # no dividends
    add_facts(con, "stg_income_annual", "CCC", [("net_income", 2024, 100.0), ("shares_diluted_weighted", 2024, 50.0)], cik=3)
    add_regular_dividends(con, "CCC", 2024, 1.2, complete=False)                                                    # year not complete
    build(con)
    for ticker in ("AAA", "BBB", "CCC"):
        r = row(con, ticker)
        assert r["dividends_paid"] is None and r["dividends_estimated"] is False and r["payout_earnings"] is None
