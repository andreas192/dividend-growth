from dgi.scoring.flags import compute_flags


def codes(row):
    return [(f.code, f.severity) for f in compute_flags(row)]


def test_a_clean_company_has_no_flags():
    assert compute_flags({"cut_years_5y": 0, "payout_earnings": 0.5, "payout_fcf": 0.6, "fcf_latest": 10.0}) == []


def test_every_red_flag_fires_with_words_the_company_page_can_show():
    row = {"cut_years_5y": 1, "payout_earnings": 1.4, "payout_fcf": 9.99, "fcf_latest": -5.0, "suspect_dividend_count": 2}
    assert codes(row) == [
        ("dividend_cut", "red"), ("payout_eps_over_100", "red"), ("payout_fcf_over_100", "red"),
        ("negative_fcf", "red"), ("suspect_dividend", "red"),
    ]
    text = {f.code: f.text for f in compute_flags(row)}
    assert "140%" in text["payout_eps_over_100"] and "free cash flow" in text["payout_fcf_over_100"]


def test_a_payout_of_exactly_100_percent_is_not_flagged():
    assert compute_flags({"payout_fcf": 1.0}) == []


def test_missing_values_never_raise_a_flag():
    assert compute_flags({}) == []
    assert compute_flags({"cut_years_5y": None, "payout_fcf": None, "fcf_latest": None}) == []


def test_specials_and_irregular_payment_counts_are_information_not_red():
    assert codes({"special_count_5y": 1, "irregular_payments": True}) == [("special_dividend", "info"), ("irregular_payments", "info")]


def test_nan_never_raises_a_numeric_flag():
    nan = float("nan")
    row = {"cut_years_5y": nan, "payout_earnings": nan, "payout_fcf": nan, "fcf_latest": nan, "suspect_dividend_count": nan, "special_count_5y": nan}
    assert compute_flags(row) == []
