import pytest

from dgi.metrics import build_metrics, metrics_version
from dgi.metrics.dividends import build_dividend_metrics
from dgi.metrics.params import MetricParams
from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import (
    D, TODAY, add_company, add_dividends, add_facts, add_prices, add_quarters, add_split, add_year_end_prices, metrics_con, quarterly,
)


def first(con, sql, *args):
    return con.execute(sql, list(args)).fetchone()


def test_year_end_close_is_restated_for_splits_after_it_and_the_last_trading_day_wins():
    con = metrics_con()
    add_prices(con, "stg_prices_yearend", "AAA", [(D(2021, 12, 29), 90.0), (D(2021, 12, 30), 100.0), (D(2023, 12, 29), 60.0)])
    add_split(con, "AAA", D(2022, 6, 1), 2.0)
    run_sql_file(con, "price_yearend")
    assert con.execute("SELECT year, close_adj FROM price_yearend ORDER BY year").fetchall() == [(2021, 50.0), (2023, 60.0)]


def test_latest_price_is_the_most_recent_bar_per_ticker():
    con = metrics_con()
    add_prices(con, "stg_prices_latest", "AAA", [(D(2026, 10, 1), 59.0), (D(2026, 10, 2), 60.0)])
    run_sql_file(con, "price_dividend")
    assert first(con, "SELECT price, price_date FROM tmp_price") == (60.0, D(2026, 10, 2))


def test_dividend_ttm_counts_regular_payments_in_the_last_365_days_and_specials_over_five_years():
    con = metrics_con()  # today 2026-10-05: window starts 2025-10-05
    add_dividends(con, "AAA", [p for y in range(2019, 2026) for p in quarterly(y, 0.25)] + [(D(2026, 2, 10), 0.26), (D(2026, 5, 10), 0.26), (D(2026, 8, 10), 0.26), (D(2024, 12, 20), 3.0)])
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    run_sql_file(con, "price_dividend")
    ttm, specials = first(con, "SELECT dividend_ttm, special_count_5y FROM tmp_dividend_ttm")
    assert ttm == pytest.approx(0.25 + 0.26 * 3)  # 2025-11-10 plus three 2026 payments
    assert specials == 1


def test_average_yield_needs_three_complete_years_with_prices_and_the_suspect_count_flags_impossible_dividends():
    con = metrics_con()
    add_dividends(con, "AAA", [p for y in range(2020, 2026) for p in quarterly(y, 0.5)])
    add_year_end_prices(con, "AAA", {2020: 50.0, 2021: 50.0, 2022: 50.0, 2023: 50.0, 2024: 50.0, 2025: 50.0})
    add_dividends(con, "BBB", [p for y in range(2020, 2026) for p in quarterly(y, 60.0)])  # quarterly dividend above the share price
    add_year_end_prices(con, "BBB", {2020: 50.0, 2021: 50.0, 2022: 50.0, 2023: 50.0, 2024: 50.0, 2025: 50.0})
    add_dividends(con, "CCC", [p for y in range(2024, 2026) for p in quarterly(y, 0.5)])
    add_year_end_prices(con, "CCC", {2023: 50.0, 2024: 50.0, 2025: 50.0})
    run_sql_file(con, "price_yearend")
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")
    run_sql_file(con, "price_dividend")
    assert first(con, "SELECT div_yield_avg_5y FROM tmp_yield_avg WHERE ticker = 'AAA'")[0] == pytest.approx(2.0 / 50.0)
    assert first(con, "SELECT count(*) FROM tmp_yield_avg WHERE ticker = 'CCC'") == (0,)  # only two complete years
    assert first(con, "SELECT suspect_dividend_count FROM tmp_suspect WHERE ticker = 'BBB'")[0] == 20  # 2021-2025, four payments each
    assert first(con, "SELECT count(*) FROM tmp_suspect WHERE ticker = 'AAA'") == (0,)


def fund_facts(con, ticker="AAA", years=range(2015, 2026), cik=1):
    """Eleven fiscal years of a steady grower: revenue +4%, net income +5%, shares -1% a year."""
    for fy in years:
        n = fy - 2015
        add_facts(con, "stg_income_annual", ticker, [
            ("revenue", fy, 1000.0 * 1.04 ** n), ("net_income", fy, 100.0 * 1.05 ** n), ("operating_income", fy, 150.0 * 1.05 ** n),
            ("interest_expense", fy, 10.0), ("shares_diluted_weighted", fy, 100.0 * 0.99 ** n), ("eps_diluted", fy, 100.0 * 1.05 ** n / (100.0 * 0.99 ** n)),
        ], cik=cik)
        add_facts(con, "stg_cash_annual", ticker, [
            ("cash_from_operations", fy, 120.0 * 1.05 ** n), ("capex", fy, -20.0), ("dividends_paid", fy, -50.0 * 1.05 ** n), ("depreciation_amortization", fy, 30.0),
        ], cik=cik)
        add_facts(con, "stg_balance_annual", ticker, [
            ("total_equity", fy, 500.0), ("current_assets", fy, 300.0), ("current_liabilities", fy, 150.0), ("cash_and_equivalents", fy, 50.0), ("long_term_debt", fy, 200.0),
        ], cik=cik)


def test_fund_metrics_summarize_the_latest_year_and_the_windows():
    con = metrics_con()
    fund_facts(con)
    run_sql_file(con, "fundamentals_annual")
    run_sql_file(con, "fund_metrics")
    cur = con.execute("SELECT * FROM tmp_fund_metrics")
    r = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    assert r["latest_fy"] == 2025 and r["fy_period_end"] == D(2025, 12, 31)
    assert r["payout_earnings"] == pytest.approx(0.5) and r["payout_earnings_5y"] == pytest.approx(0.5)
    assert r["payout_fcf"] == pytest.approx(50 * 1.05 ** 10 / (120 * 1.05 ** 10 - 20))
    assert r["interest_coverage"] == pytest.approx(150 * 1.05 ** 10 / 10)
    assert r["net_debt_ebitda"] == pytest.approx(150 / (150 * 1.05 ** 10 + 30))
    assert r["current_ratio"] == 2.0 and r["positive_earnings_years"] == 10
    assert r["rev_cagr_5"] == pytest.approx(0.04) and r["eps_cagr_5"] == pytest.approx(1.05 / 0.99 - 1)
    assert r["share_trend_5"] == pytest.approx(-0.01) and r["roe"] == pytest.approx(100 * 1.05 ** 10 / 500)
    assert r["op_margin_std"] is not None


def test_fund_metrics_edge_cases_cap_or_leave_null():
    con = metrics_con()
    add_facts(con, "stg_income_annual", "AAA", [("net_income", 2025, 10.0), ("operating_income", 2025, 5.0)])  # no interest expense reported
    add_facts(con, "stg_balance_annual", "AAA", [("total_equity", 2025, -100.0), ("cash_and_equivalents", 2025, 1.0), ("long_term_debt", 2025, 500.0)])
    add_facts(con, "stg_cash_annual", "AAA", [("depreciation_amortization", 2025, -10.0)])  # EBITDA = 5 - 10 < 0
    add_facts(con, "stg_income_annual", "BBB", [("net_income", 2025, 10.0)], cik=2)
    add_facts(con, "stg_balance_annual", "BBB", [("cash_and_equivalents", 2025, 900.0)], cik=2)
    add_facts(con, "stg_income_annual", "BBB", [("operating_income", 2025, 100.0)], cik=2)
    add_facts(con, "stg_cash_annual", "BBB", [("depreciation_amortization", 2025, 20.0)], cik=2)
    run_sql_file(con, "fundamentals_annual")
    run_sql_file(con, "fund_metrics")
    a = first(con, "SELECT interest_coverage, net_debt_ebitda, roe FROM tmp_fund_metrics WHERE ticker = 'AAA'")
    assert a == (99.0, 99.0, None)  # no interest -> capped coverage; positive net debt on negative EBITDA -> capped; negative equity -> no ROE
    b = first(con, "SELECT net_debt_ebitda FROM tmp_fund_metrics WHERE ticker = 'BBB'")
    assert b == (0.0,)  # net cash


def ttm_quarters(con, ticker, ends, ni=30.0, cfo=35.0, capex=-6.0, shares=90.0):
    add_quarters(con, "stg_income_quarter", ticker, "net_income", ends, ni)
    add_quarters(con, "stg_income_quarter", ticker, "shares_diluted_weighted", ends, shares)
    add_quarters(con, "stg_cash_quarter", ticker, "cash_from_operations", ends, cfo)
    add_quarters(con, "stg_cash_quarter", ticker, "capex", ends, capex)


FOUR = [D(2024, 12, 31), D(2025, 3, 31), D(2025, 6, 30), D(2025, 9, 30)]


def test_ttm_uses_four_consecutive_quarters_of_all_three_concepts():
    con = metrics_con()
    ttm_quarters(con, "AAA", FOUR)
    run_sql_file(con, "ttm")
    assert first(con, "SELECT ttm_end, ni_ttm, cfo_ttm, capex_ttm, shares_ttm FROM tmp_ttm") == (D(2025, 9, 30), 120.0, 140.0, 24.0, 90.0)


def test_ttm_is_absent_when_a_quarter_is_missing_the_span_is_wrong_or_a_concept_ends_earlier():
    con = metrics_con()
    ttm_quarters(con, "AAA", FOUR[:3] + [D(2023, 9, 30)])          # fourth quarter a year earlier: span too long
    ttm_quarters(con, "BBB", FOUR[:3])                              # only three quarters
    ttm_quarters(con, "CCC", FOUR)
    con.execute("DELETE FROM stg_cash_quarter WHERE ticker = 'CCC' AND period_end = DATE '2025-09-30'")  # cash flow ends a quarter earlier
    run_sql_file(con, "ttm")
    assert first(con, "SELECT count(*) FROM tmp_ttm") == (0,)


def test_ttm_shares_are_restated_for_splits_after_the_filing():
    con = metrics_con()
    ttm_quarters(con, "AAA", FOUR)
    add_split(con, "AAA", D(2026, 1, 5), 2.0)  # after the latest filing (2025-11-09)
    run_sql_file(con, "ttm")
    assert first(con, "SELECT shares_ttm FROM tmp_ttm") == (180.0,)


def test_shares_out_prefers_the_cover_count_over_the_balance_sheet_count_on_the_same_date():
    con = metrics_con()
    add_quarters(con, "stg_shares_cover", "AAA", "shares_outstanding", [D(2025, 9, 30)], 80.0)
    add_quarters(con, "stg_shares_cover", "AAA", "shares_outstanding_cover", [D(2025, 9, 30)], 88.0)
    run_sql_file(con, "shares_out")
    assert first(con, "SELECT shares_out FROM tmp_shares_out") == (88.0,)


def staged_grower(con, ticker="AAA", with_quarters=False):
    add_company(con, ticker)
    add_dividends(con, ticker, [p for y in range(2014, 2026) for p in quarterly(y, 0.25 * 1.05 ** (y - 2014))] + quarterly(2026, 0.25 * 1.05 ** 12)[:3])
    fund_facts(con, ticker)
    add_year_end_prices(con, ticker, {y: 20.0 * 1.1 ** (y - 2014) for y in range(2014, 2026)})
    add_prices(con, "stg_prices_latest", ticker, [(D(2026, 10, 2), 60.0)])
    add_quarters(con, "stg_shares_cover", ticker, "shares_outstanding_cover", [D(2025, 12, 31)], 88.0)
    if with_quarters:
        ttm_quarters(con, ticker, [D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30), D(2026, 9, 30)], ni=40.0, shares=88.0)


def test_build_metrics_end_to_end_for_a_payer_and_a_company_without_dividends():
    con = metrics_con()
    staged_grower(con)
    add_company(con, "NEW", cik=2)
    result = build_metrics(con, TODAY, MetricParams())
    assert (result.companies, result.with_streak) == (2, 1) and result.dividend_payments > 40
    cur = con.execute("SELECT * FROM metrics_current WHERE ticker = 'AAA'")
    m = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    assert (m["streak"], m["no_cut_streak"], m["years_history"], m["payment_frequency"]) == (11, 11, 12, 4)
    assert m["dgr_5"] == pytest.approx(0.05) and m["dgr_10"] == pytest.approx(0.05)
    assert m["price"] == 60.0 and m["market_cap"] == 60.0 * 88.0
    assert m["dividend_ttm"] == pytest.approx(0.25 * 1.05 ** 11 + 0.25 * 1.05 ** 12 * 3)  # Nov 2025 plus three 2026 payments
    assert m["basis"] == "fy" and m["latest_fy"] == 2025
    assert m["pe"] == pytest.approx(60.0 / (100 * 1.05 ** 10 / (100 * 0.99 ** 10)))
    assert m["div_yield_avg_5y"] is not None and m["yield_vs_avg"] is not None
    assert m["cut_years_5y"] == 0 and m["special_count_5y"] == 0 and m["suspect_dividend_count"] == 0
    n = con.execute("SELECT streak, price, latest_fy, special_count_5y, suspect_dividend_count FROM metrics_current WHERE ticker = 'NEW'").fetchone()
    assert n == (None, None, None, 0, 0)


def test_build_metrics_switches_to_the_ttm_basis_when_quarters_run_past_the_fiscal_year():
    con = metrics_con()
    staged_grower(con, with_quarters=True)
    build_metrics(con, TODAY, MetricParams())
    m = con.execute("SELECT basis, eps_basis, fcf_ps_basis FROM metrics_current WHERE ticker = 'AAA'").fetchone()
    assert m[0] == "ttm" and m[1] == pytest.approx(160.0 / 88.0)
    assert m[2] == pytest.approx((140.0 - 24.0) / 88.0)


def test_build_metrics_can_run_twice_on_the_same_connection():
    con = metrics_con()
    staged_grower(con)
    build_metrics(con, TODAY, MetricParams())
    again = build_metrics(con, TODAY, MetricParams())
    assert again.companies == 1


def test_metrics_version_changes_with_the_metrics_params_only():
    base = metrics_version(MetricParams())
    assert metrics_version(MetricParams()) == base
    assert metrics_version(MetricParams(cut_tolerance=0.02)) != base
