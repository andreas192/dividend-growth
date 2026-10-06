"""A tiny investment-API dataset built in code: one dividend grower, a bank with the same numbers,
a company with a short dividend history, and one that pays nothing. Values are chosen so the
traced results are easy to state: ACME has raised its dividend 11 years in a row (2015-2025)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pyarrow as pa

D = dt.date
GROWER_YEARS = range(2014, 2026)
QUARTER_ENDS = [D(2025, 9, 30), D(2025, 12, 31), D(2026, 3, 31), D(2026, 6, 30)]  # TTM ends after the 2025 fiscal year

COMPANIES = [
    (1001, "Acme Beverages", "ACME", "2080", "Beverages"),
    (1002, "Banky Financial", "BANKY", "6021", "National commercial banks"),
    (1003, "Newco Industries", "NEWCO", "3000", "Misc manufacturing"),
    (1004, "Nopay Software", "NOPAY", "7370", "Computer services"),
]

STATEMENT_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("cik", pa.int64()), ("concept", pa.string()), ("period_kind", pa.string()),
    ("period_start", pa.date32()), ("period_end", pa.date32()), ("fiscal_year", pa.int32()), ("fiscal_period", pa.string()),
    ("value", pa.float64()), ("unit", pa.string()), ("filed", pa.date32()), ("source_accession", pa.string()), ("data_flag", pa.string()),
])


def _statement_row(ticker: str, cik: int, concept: str, kind: str, end: dt.date, fy: int, period: str, value: float, lag: int) -> dict[str, Any]:
    start = None if kind == "instant" else end - dt.timedelta(days=364 if kind == "annual" else 89)
    return {"ticker": ticker, "cik": cik, "concept": concept, "period_kind": kind, "period_start": start, "period_end": end,
            "fiscal_year": fy, "fiscal_period": period, "value": value, "unit": "USD", "filed": end + dt.timedelta(days=lag),
            "source_accession": f"{cik}-{fy}", "data_flag": None}


def grower_statements(ticker: str, cik: int) -> dict[str, list[dict[str, Any]]]:
    income, cash, balance = [], [], []
    for fy in GROWER_YEARS:
        n, end = fy - 2014, D(fy, 12, 31)
        shares = 100e6 * 0.99 ** n
        for concept, value in (("revenue", 1000e6 * 1.04 ** n), ("net_income", 100e6 * 1.05 ** n), ("operating_income", 150e6 * 1.05 ** n),
                               ("interest_expense", 10e6), ("shares_diluted_weighted", shares), ("eps_diluted", 100e6 * 1.05 ** n / shares)):
            income.append(_statement_row(ticker, cik, concept, "annual", end, fy, "FY", value, 60))
        for concept, value in (("cash_from_operations", 120e6 * 1.05 ** n), ("capex", -20e6), ("dividends_paid", -50e6 * 1.05 ** n), ("depreciation_amortization", 30e6)):
            cash.append(_statement_row(ticker, cik, concept, "annual", end, fy, "FY", value, 60))
        for concept, value in (("total_equity", 500e6), ("current_assets", 300e6), ("current_liabilities", 150e6), ("cash_and_equivalents", 50e6), ("long_term_debt", 200e6)):
            balance.append(_statement_row(ticker, cik, concept, "instant", end, fy, "FY", value, 60))
    for end in QUARTER_ENDS:
        for concept, value in (("net_income", 40e6), ("shares_diluted_weighted", 88e6)):
            income.append(_statement_row(ticker, cik, concept, "quarter", end, end.year, "Q", value, 40))
        for concept, value in (("cash_from_operations", 35e6), ("capex", -6e6)):
            cash.append(_statement_row(ticker, cik, concept, "quarter", end, end.year, "Q", value, 40))
    balance.append(_statement_row(ticker, cik, "shares_outstanding_cover", "instant", QUARTER_ENDS[-1], 2026, "Q", 88e6, 40))
    return {"income": income, "cash": cash, "balance": balance}


def quarterly_dividends(ticker: str, cik: int, years: range, base: float, growth: float, last_year_months: tuple[int, ...] = (2, 5, 8, 11)):
    rows = []
    for year in years:
        for month in last_year_months:
            rows.append((ticker, cik, D(year, month, 10), base * (1 + growth) ** (year - years.start)))
    return rows


def build_sample_gold() -> dict[str, pa.Table]:
    income: list = []
    cash: list = []
    balance: list = []
    dividends: list = []
    prices: list = []
    for cik, ticker in ((1001, "ACME"), (1002, "BANKY")):
        facts = grower_statements(ticker, cik)
        income += facts["income"]; cash += facts["cash"]; balance += facts["balance"]
        dividends += quarterly_dividends(ticker, cik, GROWER_YEARS, 0.25, 0.05)
        dividends += [(ticker, cik, D(2026, m, 10), 0.25 * 1.05 ** 12) for m in (2, 5, 8)]
    dividends += quarterly_dividends("NEWCO", 1003, range(2024, 2026), 0.10, 0.0)
    for cik, _, ticker, _, _ in COMPANIES:
        for year in GROWER_YEARS:
            prices.append((ticker, cik, D(year, 12, 30), 20.0 * 1.1 ** (year - 2014)))
        prices += [(ticker, cik, D(2026, 10, 1), 59.0), (ticker, cik, D(2026, 10, 2), 60.0)]

    def statement(rows):
        return pa.Table.from_pylist(rows, schema=STATEMENT_SCHEMA)

    return {
        "company": pa.table({
            "cik": pa.array([c[0] for c in COMPANIES], pa.int64()), "name": [c[1] for c in COMPANIES], "ticker": [c[2] for c in COMPANIES],
            "sic": [c[3] for c in COMPANIES], "sic_description": [c[4] for c in COMPANIES]}),
        "dividend_events": pa.table({
            "ticker": [r[0] for r in dividends], "cik": pa.array([r[1] for r in dividends], pa.int64()),
            "ex_date": pa.array([r[2] for r in dividends], pa.date32()), "amount": [r[3] for r in dividends]}),
        "split_events": pa.table({
            "ticker": pa.array([], pa.string()), "cik": pa.array([], pa.int64()), "ex_date": pa.array([], pa.date32()),
            "numerator": pa.array([], pa.float64()), "denominator": pa.array([], pa.float64()), "ratio": pa.array([], pa.float64())}),
        "price_daily": pa.table({
            "ticker": [r[0] for r in prices], "cik": pa.array([r[1] for r in prices], pa.int64()),
            "trade_date": pa.array([r[2] for r in prices], pa.date32()), "open": [r[3] for r in prices], "high": [r[3] for r in prices],
            "low": [r[3] for r in prices], "close": [r[3] for r in prices], "volume": pa.array([1_000_000] * len(prices), pa.int64())}),
        "price_daily_adjusted": pa.table({
            "ticker": [r[0] for r in prices], "cik": pa.array([r[1] for r in prices], pa.int64()),
            "trade_date": pa.array([r[2] for r in prices], pa.date32()), "close": [r[3] for r in prices],
            "split_adjusted_close": [r[3] for r in prices], "total_return_close": [r[3] for r in prices]}),
        "income_statement_latest": statement(income),
        "cash_flow_latest": statement(cash),
        "balance_sheet_latest": statement(balance),
    }
