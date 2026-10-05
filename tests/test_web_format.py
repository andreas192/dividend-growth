import pytest

from dgi.web.format import big, csv_safe, metric_label, metric_value, money, num, pct, reason_label, score, streak_label


def test_none_shows_as_a_dash_everywhere():
    assert [f(None) for f in (pct, num, money, big, score)] == ["–"] * 5
    assert metric_value("streak", None) == "–"


def test_numbers():
    assert pct(0.0345) == "3.5%" and pct(0.0345, 0) == "3%" and pct(-0.1) == "-10.0%"
    assert num(1234.567) == "1,234.6" and num(1234.567, 0) == "1,235"
    assert money(60) == "$60.00" and money(1234.4, 0) == "$1,234"
    assert score(86.6) == "87"


@pytest.mark.parametrize("value, expected", [(5e12, "$5.0T"), (2.5e9, "$2.5B"), (7.2e6, "$7.2M"), (950.0, "$950")])
def test_big_picks_a_unit(value, expected):
    assert big(value) == expected


def test_metric_values_follow_their_kind():
    assert metric_value("dgr_5", 0.0512) == "5.1%"
    assert metric_value("streak", 11.0) == "11"
    assert metric_value("pe", 18.456) == "18.46"
    assert metric_value("payout_fcf", 9.99) == "999.0%"


def test_labels():
    assert metric_label("dgr_5") == "Dividend growth, 5-year CAGR" and metric_label("unknown") == "unknown"
    assert "REIT" in reason_label("financial_or_reit") and reason_label(None) == "–" and reason_label("odd") == "odd"


@pytest.mark.parametrize("text, expected", [("=SUM(A1)", "'=SUM(A1)"), ("+1", "'+1"), ("-1", "'-1"), ("@x", "'@x"), ("Coca-Cola", "Coca-Cola"), (None, ""), (5, "5")])
def test_csv_safe_blocks_formula_injection(text, expected):
    assert csv_safe(text) == expected


@pytest.mark.parametrize("streak, history, expected", [(30, 45, "30"), (55, 56, "55+"), (12, 13, "12+"), (0, 1, "0"), (None, 10, "–"), (5, None, "5")])
def test_streak_label_adds_a_plus_when_the_record_reaches_the_start_of_the_history(streak, history, expected):
    assert streak_label(streak, history) == expected
