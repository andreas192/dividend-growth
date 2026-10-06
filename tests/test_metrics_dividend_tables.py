from dgi.metrics.sqlrun import run_sql_file
from tests.metrics_fixtures import D, add_dividends, add_split, metrics_con, quarterly


def build(con):
    run_sql_file(con, "dividend_payment")
    run_sql_file(con, "dividend_annual")


def annual(con, ticker):
    rows = con.execute(
        "SELECT year, round(dps, 6), n_payments, round(special_total, 6), complete FROM dividend_annual WHERE ticker = ? ORDER BY year", [ticker]
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_a_split_restates_older_payments_so_it_creates_no_fake_cut():
    con = metrics_con()
    # 2-for-1 on 2022-06-01: payments before it were paid on the old share basis (1.00), after it 0.50
    add_dividends(con, "AAA", quarterly(2021, 1.0) + quarterly(2022, 1.0)[:2] + quarterly(2022, 0.5)[2:] + quarterly(2023, 0.5))
    add_split(con, "AAA", D(2022, 6, 1), 2.0)
    build(con)
    got = annual(con, "AAA")
    assert got[2021] == (2.0, 4, 0.0, True)
    assert got[2022] == (2.0, 4, 0.0, True)
    assert got[2023] == (2.0, 4, 0.0, True)


def test_a_reverse_split_scales_the_other_way():
    con = metrics_con()
    add_dividends(con, "AAA", quarterly(2021, 0.1) + quarterly(2023, 1.0))
    add_split(con, "AAA", D(2022, 1, 3), 0.1)  # 1-for-10 reverse split: ratio 0.1
    build(con)
    assert annual(con, "AAA")[2021][0] == 4.0 and annual(con, "AAA")[2023][0] == 4.0


def test_a_special_dividend_is_excluded_from_the_total_and_recorded():
    con = metrics_con()
    regular = [p for y in range(2019, 2024) for p in quarterly(y, 0.25)]
    add_dividends(con, "AAA", regular + [(D(2023, 12, 20), 3.0)])
    build(con)
    assert annual(con, "AAA")[2023] == (1.0, 4, 3.0, True)
    assert con.execute("SELECT ex_date FROM dividend_payment WHERE is_special").fetchall() == [(D(2023, 12, 20),)]


def test_an_extra_payment_below_the_special_ratio_stays_in_the_total():
    con = metrics_con()
    regular = [p for y in range(2019, 2024) for p in quarterly(y, 0.25)]
    add_dividends(con, "AAA", regular + [(D(2023, 12, 20), 0.30)])  # 0.30 < 1.5 x 0.25
    build(con)
    assert annual(con, "AAA")[2023] == (1.3, 5, 0.0, True)


def test_the_current_year_is_not_complete_and_future_dated_rows_are_ignored():
    con = metrics_con()  # today is 2026-10-05
    add_dividends(con, "AAA", [p for y in range(2023, 2026) for p in quarterly(y, 0.25)] + [(D(2026, 2, 10), 0.25), (D(2026, 11, 10), 0.25)])
    build(con)
    got = annual(con, "AAA")
    assert got[2026] == (0.25, 1, 0.0, False)
    assert got[2025][3] is True


def test_zero_and_negative_amounts_are_ignored():
    con = metrics_con()
    add_dividends(con, "AAA", quarterly(2024, 0.25) + [(D(2024, 12, 1), 0.0), (D(2024, 12, 2), -0.1)])
    build(con)
    assert annual(con, "AAA")[2024] == (1.0, 4, 0.0, True)


def test_payments_of_other_tickers_do_not_mix():
    con = metrics_con()
    add_dividends(con, "AAA", quarterly(2024, 0.25))
    add_dividends(con, "BBB", quarterly(2024, 1.0, months=(3, 9)))
    build(con)
    assert annual(con, "AAA")[2024][0] == 1.0 and annual(con, "BBB")[2024][0] == 2.0
