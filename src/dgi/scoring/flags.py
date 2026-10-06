from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PAYOUT_LIMIT = 1.0
YIELD_OUTLIER = 0.25


@dataclass(frozen=True)
class Flag:
    code: str
    severity: str
    text: str


def _suspended(m: Mapping[str, Any]) -> bool:
    """A former payer that stopped more than five years ago: no payments now, history on record, no cut inside the window."""
    years = m.get("years_history")
    return m.get("payment_frequency") is None and years is not None and years > 0 and m.get("cut_years_5y") == 0


def compute_flags(m: Mapping[str, Any]) -> list[Flag]:
    flags: list[Flag] = []
    if (m.get("cut_years_5y") or 0) > 0:
        flags.append(Flag("dividend_cut", "red", "The dividend was cut or suspended in the last 5 years."))
    if _suspended(m):
        flags.append(Flag("dividend_suspended", "red", "Dividend payments have stopped."))
    for key, code, label in (("payout_earnings", "payout_eps_over_100", "earnings"), ("payout_fcf", "payout_fcf_over_100", "free cash flow")):
        payout = m.get(key)
        if payout is not None and payout > PAYOUT_LIMIT:
            flags.append(Flag(code, "red", f"Dividends paid exceed {label} (payout {payout:.0%})."))
    fcf = m.get("fcf_latest")
    if fcf is not None and fcf < 0:
        flags.append(Flag("negative_fcf", "red", "Free cash flow was negative in the latest fiscal year."))
    if (m.get("suspect_dividend_count") or 0) > 0:
        flags.append(Flag("suspect_dividend", "red", "A dividend in the price data is at or above the share price; likely a data error."))
    div_yield = m.get("div_yield")
    if div_yield is not None and div_yield > YIELD_OUTLIER:
        flags.append(Flag("yield_outlier", "red", f"The dividend yield ({div_yield:.0%}) is more likely a data error or distress than an opportunity."))
    if (m.get("special_count_5y") or 0) > 0:
        flags.append(Flag("special_dividend", "info", "A special dividend in the last 5 years is excluded from the growth figures."))
    if m.get("irregular_payments"):
        flags.append(Flag("irregular_payments", "info", "The number of payments last year differs from the usual count; growth figures may be distorted."))
    return flags
