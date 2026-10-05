"""Number and metric formatting for templates. Pure functions; None shows as a dash."""

from __future__ import annotations

from typing import Any

DASH = "–"

METRIC_LABELS = {
    "streak": "Years of dividend increases", "no_cut_streak": "Years without a cut", "dgr_1": "Dividend growth, 1 year",
    "dgr_3": "Dividend growth, 3-year CAGR", "dgr_5": "Dividend growth, 5-year CAGR", "dgr_10": "Dividend growth, 10-year CAGR",
    "yield_vs_avg": "Yield vs its 5-year average", "payment_frequency": "Payments per year",
    "payout_earnings": "Payout of earnings", "payout_earnings_5y": "Payout of earnings, 5-year", "payout_fcf": "Payout of free cash flow",
    "payout_fcf_5y": "Payout of free cash flow, 5-year", "interest_coverage": "Interest coverage", "net_debt_ebitda": "Net debt / EBITDA",
    "current_ratio": "Current ratio", "positive_earnings_years": "Profitable years in the last 10",
    "rev_cagr_5": "Revenue growth, 5-year CAGR", "eps_cagr_5": "EPS growth, 5-year CAGR", "fcf_ps_cagr_5": "FCF per share growth, 5-year CAGR",
    "roe": "Return on equity", "op_margin_std": "Operating margin volatility", "share_trend_5": "Share count change, 5-year CAGR",
    "pe": "P/E", "p_fcf": "P/FCF", "fcf_yield": "FCF yield", "margin_of_safety": "Margin of safety vs fair value",
}
PERCENT_METRICS = {
    "dgr_1", "dgr_3", "dgr_5", "dgr_10", "yield_vs_avg", "payout_earnings", "payout_earnings_5y", "payout_fcf", "payout_fcf_5y",
    "rev_cagr_5", "eps_cagr_5", "fcf_ps_cagr_5", "roe", "op_margin_std", "share_trend_5", "fcf_yield", "margin_of_safety",
}
INTEGER_METRICS = {"streak", "no_cut_streak", "payment_frequency", "positive_earnings_years"}
PILLAR_LABELS = {"dividend": "Dividend record", "safety": "Safety", "growth": "Growth and quality", "valuation": "Valuation"}
REASON_LABELS = {
    "financial_or_reit": "Bank, insurer or REIT: payout and coverage ratios do not describe these businesses, so they are not scored in v1",
    "short_dividend_history": "Fewer complete years of dividend history than the minimum",
    "no_price": "No recent price",
    "insufficient_data": "Too little data to score reliably",
}


UNITS = ((1e12, "T"), (1e9, "B"), (1e6, "M"))


def _signed(value: float, body: str) -> str:
    """The minus sign goes first, and a negative that rounds to zero shows no sign."""
    return "-" + body if value < 0 and body.strip("0.,$%TBM") else body


def pct(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else _signed(value, f"{abs(value) * 100:.{digits}f}%")


def num(value: float | None, digits: int = 1) -> str:
    return DASH if value is None else _signed(value, f"{abs(value):,.{digits}f}")


def money(value: float | None, digits: int = 2) -> str:
    return DASH if value is None else _signed(value, f"${abs(value):,.{digits}f}")


def _big_body(magnitude: float) -> str:
    for i, (limit, suffix) in enumerate(UNITS):
        if magnitude >= limit:
            if round(magnitude / limit, 1) >= 1000 and i > 0:
                limit, suffix = UNITS[i - 1]  # 999.96M shows as 1.0B, not 1,000.0M
            return f"${magnitude / limit:,.1f}{suffix}"
    rounded = round(magnitude)
    return f"${rounded:,}" if rounded < UNITS[-1][0] else "$1.0M"


def big(value: float | None) -> str:
    return DASH if value is None else _signed(value, _big_body(abs(value)))


def score(value: float | None) -> str:
    return DASH if value is None else f"{value:.0f}"


def streak_label(value: float | None, years_history: float | None) -> str:
    """The streak, with a plus when it reaches back to the start of the available dividend history (the real record may be longer)."""
    if value is None:
        return DASH
    capped = value > 0 and years_history is not None and value >= years_history - 1
    return f"{value:.0f}+" if capped else f"{value:.0f}"


def metric_label(name: str) -> str:
    return METRIC_LABELS.get(name, name)


def metric_value(name: str, value: float | None) -> str:
    if value is None:
        return DASH
    if name in PERCENT_METRICS:
        return pct(value)
    if name in INTEGER_METRICS:
        return f"{value:.0f}"
    return num(value, 2)


def reason_label(reason: str | None) -> str:
    return REASON_LABELS.get(reason or "", reason or DASH)


def csv_safe(text: Any) -> str:
    """Stop a spreadsheet from running a cell: text starting with a tab or CR, or whose first non-blank character is = + - @, gets a leading apostrophe."""
    value = "" if text is None else str(text)
    return "'" + value if value[:1] in ("\t", "\r") or value.lstrip()[:1] in ("=", "+", "-", "@") else value
