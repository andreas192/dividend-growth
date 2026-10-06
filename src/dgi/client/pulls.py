"""What the refresh pulls from the API, as data: one Pull per request family."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

STATEMENT_YEARS = 12          # fiscal years back from the current year (11 full years plus a margin)
PRICE_YEAREND_YEARS = 12      # December windows back from last year
QUARTER_DAYS = 800            # recent quarters for the TTM basis
SHARES_DAYS = 730             # cover-page share counts

INCOME_ANNUAL = "revenue,net_income,operating_income,eps_diluted,interest_expense,shares_diluted_weighted"
CASH_ANNUAL = "cash_from_operations,capex,dividends_paid,depreciation_amortization"
BALANCE_ANNUAL = (
    "total_equity,current_assets,current_liabilities,cash_and_equivalents,short_term_investments,"
    "long_term_debt,short_term_debt,current_portion_long_term_debt"
)
SHARES = "shares_outstanding_cover,shares_outstanding"


@dataclass(frozen=True)
class Pull:
    table: str
    resource: str
    filters: dict[str, str]


def build_pulls(today: date) -> list[Pull]:
    first_fy = str(today.year - STATEMENT_YEARS)
    quarters_from = (today - timedelta(days=QUARTER_DAYS)).isoformat()
    shares_from = (today - timedelta(days=SHARES_DAYS)).isoformat()
    pulls = [
        Pull("stg_company", "company", {}),
        Pull("stg_dividends", "dividend_events", {}),
        Pull("stg_splits", "split_events", {}),
        Pull("stg_income_annual", "income_statement_latest", {"period_kind": "annual", "concept": INCOME_ANNUAL, "fiscal_year__gte": first_fy}),
        Pull("stg_cash_annual", "cash_flow_latest", {"period_kind": "annual", "concept": CASH_ANNUAL, "fiscal_year__gte": first_fy}),
        Pull("stg_balance_annual", "balance_sheet_latest", {"period_kind": "instant", "fiscal_period": "FY", "concept": BALANCE_ANNUAL, "fiscal_year__gte": first_fy}),
        Pull("stg_shares_cover", "balance_sheet_latest", {"period_kind": "instant", "concept": SHARES, "period_end__gte": shares_from}),
        Pull("stg_income_quarter", "income_statement_latest", {"period_kind": "quarter", "concept": "net_income,shares_diluted_weighted", "period_end__gte": quarters_from}),
        Pull("stg_cash_quarter", "cash_flow_latest", {"period_kind": "quarter", "concept": "cash_from_operations,capex", "period_end__gte": quarters_from}),
        Pull("stg_prices_latest", "price_daily", {"trade_date__gte": (today - timedelta(days=10)).isoformat()}),
    ]
    for year in range(today.year - PRICE_YEAREND_YEARS, today.year):
        pulls.append(Pull("stg_prices_yearend", "price_daily", {"trade_date__gte": f"{year}-12-20", "trade_date__lte": f"{year}-12-31"}))
    return pulls
