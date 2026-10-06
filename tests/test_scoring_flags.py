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


def test_a_former_payer_with_no_cut_in_the_window_is_flagged_as_suspended():
    row = {"payment_frequency": None, "years_history": 12, "cut_years_5y": 0}
    assert codes(row) == [("dividend_suspended", "red")]
    assert compute_flags(row)[0].text == "Dividend payments have stopped."


def test_dividend_suspended_needs_known_history_and_no_cut_already_flagged():
    assert compute_flags({"payment_frequency": None, "years_history": 0, "cut_years_5y": 0}) == []  # never paid
    assert compute_flags({"payment_frequency": 4, "years_history": 12, "cut_years_5y": 0}) == []  # still paying
    assert codes({"payment_frequency": None, "years_history": 12, "cut_years_5y": 1}) == [("dividend_cut", "red")]  # the cut flag covers it
    assert compute_flags({"payment_frequency": None, "years_history": 12}) == []  # unknown cut count
    assert compute_flags({"payment_frequency": None, "years_history": None, "cut_years_5y": 0}) == []
    nan = float("nan")
    assert compute_flags({"payment_frequency": None, "years_history": nan, "cut_years_5y": nan}) == []


def test_a_yield_above_25_percent_is_flagged_as_more_likely_wrong_than_attractive():
    flags = compute_flags({"div_yield": 0.31})
    assert [(f.code, f.severity) for f in flags] == [("yield_outlier", "red")]
    assert "31%" in flags[0].text and "data error" in flags[0].text and "distress" in flags[0].text


def test_a_yield_of_exactly_25_percent_missing_or_nan_is_not_flagged():
    assert compute_flags({"div_yield": 0.25}) == [] and compute_flags({"div_yield": None}) == [] and compute_flags({"div_yield": float("nan")}) == []
