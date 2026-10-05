"""Chart data for the company page: pure builders over cache rows, plus the daily-price cache."""

from __future__ import annotations

import math
import threading
from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import accumulate
from typing import Any, Protocol

from dgi.cache import AnnualData

PAYOUT_DISPLAY_CAP = 2.0      # payout ratios above 200% are drawn at 200% (the table keeps the real value)
COVERAGE_DISPLAY_CAP = 50.0
LEVERAGE_DISPLAY_CAP = 10.0


@dataclass(frozen=True)
class ChartSeries:
    name: str
    values: list[float | None]
    slot: int  # categorical colour slot 1..3, assigned by series order and never by rank


@dataclass(frozen=True)
class ChartSpec:
    id: str
    title: str
    kind: str                       # "bar" | "line"
    unit: str                       # "usd" | "pct" | "x" | "millions"
    x: list[Any]
    series: list[ChartSeries]
    x_type: str = "category"        # "category" (years) | "time" (ISO dates)
    note: str | None = None
    references: list[dict[str, Any]] = field(default_factory=list)  # [{"name": "100%", "value": 1.0}]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "unit": self.unit, "x": self.x, "xType": self.x_type,
            "series": [{"name": s.name, "values": [_finite(v) for v in s.values], "slot": s.slot} for s in self.series],
            "note": self.note, "references": self.references,
        }


def _finite(value: float | None) -> float | None:
    """JSON has no NaN or infinity: they become null."""
    return value if value is not None and math.isfinite(value) else None


def _present(values: Sequence[float | None]) -> bool:
    return any(_finite(v) is not None for v in values)


def _years(rows: Sequence[Mapping[str, Any]], key: str) -> list[int]:
    return [r[key] for r in rows]


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _clamp(value: float | None, cap: float) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    return (cap, True) if value > cap else (value, False)


def dividend_charts(data: AnnualData) -> list[ChartSpec]:
    rows = [r for r in data.dividends if r["complete"] and r["dps"] is not None]
    if not rows:
        return []
    years = _years(rows, "year")
    dps = [r["dps"] for r in rows]
    growth = [None] + [(b / a - 1) if a > 0 else None for a, b in zip(dps, dps[1:])]
    charts = [ChartSpec("dps", "Dividend per share by year", "bar", "usd", years, [ChartSeries("Dividend per share", dps, 1)],
                        note="Split-adjusted regular payments of each complete calendar year; special dividends are excluded.")]
    if _present(growth):
        charts.append(ChartSpec("dps_growth", "Dividend growth, year over year", "line", "pct", years, [ChartSeries("Growth", growth, 1)]))
    return charts


def yield_chart(data: AnnualData, average: float | None) -> ChartSpec | None:
    rows = [r for r in data.dividends if r["complete"] and r["dps"] is not None and r["year"] in data.year_end_prices]
    if not rows:
        return None
    values = [_ratio(r["dps"], data.year_end_prices[r["year"]]) for r in rows]
    refs = [] if average is None else [{"name": "5-year average", "value": average}]
    return ChartSpec("yield", "Dividend yield at each year end", "line", "pct", _years(rows, "year"), [ChartSeries("Yield", values, 1)],
                     note="Annual dividend per share over the year-end close, both on today's share basis.", references=refs)


def fundamentals_charts(data: AnnualData) -> list[ChartSpec]:
    rows = data.fundamentals
    if not rows:
        return []
    years = _years(rows, "fiscal_year")
    charts: list[ChartSpec] = []
    clamped = False
    payout_series = []
    for name, key, slot in (("Of earnings", "payout_earnings", 1), ("Of free cash flow", "payout_fcf", 2)):
        values = []
        for r in rows:
            value, was_clamped = _clamp(r[key], PAYOUT_DISPLAY_CAP)
            clamped = clamped or was_clamped
            values.append(value)
        payout_series.append(ChartSeries(name, values, slot))
    if any(_present(s.values) for s in payout_series):
        note = "Cash dividends paid over net income and over free cash flow. Values above 200% are drawn at 200%." if clamped else \
            "Cash dividends paid over net income and over free cash flow."
        charts.append(ChartSpec("payout", "Payout ratios", "line", "pct", years, payout_series, note=note, references=[{"name": "100%", "value": 1.0}]))
    dps_by_year = {r["year"]: r["dps"] for r in data.dividends if r["complete"]}
    per_share = [
        ChartSeries("EPS", [r["eps_diluted"] for r in rows], 1),
        ChartSeries("Free cash flow per share", [r["fcf_per_share"] for r in rows], 2),
        ChartSeries("Dividend per share", [dps_by_year.get(r["fiscal_year"]) for r in rows], 3),
    ]
    if _present(per_share[0].values) or _present(per_share[1].values):
        charts.append(ChartSpec("per_share", "EPS, free cash flow and dividend per share", "line", "usd", years, per_share,
                                note="Per-share figures are restated to today's share basis; dividends are matched by calendar year."))
    leverage = [_ratio_or_none(r["net_debt"], r["ebitda"], LEVERAGE_DISPLAY_CAP) for r in rows]
    if _present(leverage):
        charts.append(ChartSpec("leverage", "Net debt / EBITDA", "line", "x", years, [ChartSeries("Net debt / EBITDA", leverage, 1)],
                                note="Lower is safer. Net cash shows as zero; values above 10 are drawn at 10."))
    coverage = [_coverage(r["operating_income"], r["interest_expense"]) for r in rows]
    if _present(coverage):
        charts.append(ChartSpec("coverage", "Interest coverage", "line", "x", years, [ChartSeries("Operating income / interest", coverage, 1)],
                                note="Higher is safer. Values above 50 are drawn at 50; companies with no interest expense show 50."))
    shares = [None if r["shares_diluted"] is None else r["shares_diluted"] / 1e6 for r in rows]
    if _present(shares):
        charts.append(ChartSpec("shares", "Diluted share count", "line", "millions", years, [ChartSeries("Shares (millions)", shares, 1)],
                                note="Falling means buybacks. Restated to today's share basis."))
    return charts


def _ratio_or_none(net_debt: float | None, ebitda: float | None, cap: float) -> float | None:
    if net_debt is None or ebitda is None:
        return None
    if net_debt <= 0:
        return 0.0
    return cap if ebitda <= 0 else min(net_debt / ebitda, cap)


def _coverage(operating_income: float | None, interest_expense: float | None) -> float | None:
    if operating_income is None:
        return None
    if interest_expense is None or interest_expense <= 0:
        return COVERAGE_DISPLAY_CAP
    return min(operating_income / interest_expense, COVERAGE_DISPLAY_CAP)


def annual_charts(data: AnnualData, average_yield: float | None) -> list[ChartSpec]:
    """All the company page's charts that come from the cache. Charts without any data are left out."""
    charts = dividend_charts(data)
    annual_yield = yield_chart(data, average_yield)
    if annual_yield:
        charts.insert(1, annual_yield)
    return charts + fundamentals_charts(data)


# daily series (drill-down) -------------------------------------------------------------------------------------------

DailyPoints = Sequence[tuple[date, float]]


class PriceSource(Protocol):
    def daily(self, ticker: str) -> list[tuple[date, float]]:
        """Split-adjusted daily closes, oldest first. Raises a DgiError when the API cannot answer."""


def trailing_yield(points: DailyPoints, payments: Sequence[tuple[date, float]]) -> list[float | None]:
    """Yield on each day: regular payments of the previous 365 days over that day's split-adjusted close.

    Days within a year of the first payment are None, because the trailing window would be incomplete.
    """
    if not payments:
        return [None] * len(points)
    dates = [d for d, _ in payments]
    prefix = [0.0, *accumulate(a for _, a in payments)]
    first = dates[0] + timedelta(days=365)
    values: list[float | None] = []
    for day, close in points:
        if not (math.isfinite(close) and close > 0) or day < first:
            values.append(None)
            continue
        high, low = bisect_right(dates, day), bisect_right(dates, day - timedelta(days=365))
        total = prefix[high] - prefix[low]
        values.append(total / close if total > 0 else None)
    return values


def daily_charts(points: DailyPoints, payments: Sequence[tuple[date, float]], average_yield: float | None) -> list[ChartSpec]:
    if not points:
        return []
    days = [d.isoformat() for d, _ in points]
    price = ChartSpec("price", "Share price (split-adjusted)", "line", "usd", days, [ChartSeries("Close", [c for _, c in points], 1)], x_type="time")
    refs = [] if average_yield is None else [{"name": "5-year average", "value": average_yield}]
    yields = trailing_yield(points, payments)
    charts = [price]
    if _present(yields):
        charts.append(ChartSpec("yield_daily", "Dividend yield, daily", "line", "pct", days, [ChartSeries("Yield", yields, 1)], x_type="time",
                                note="Regular payments of the previous 365 days over the close.", references=refs))
    return charts


class SeriesCache:
    """Daily series held in memory, keyed by (upstream key, ticker): a new upstream key never sees an old entry."""

    def __init__(self, max_entries: int = 64) -> None:
        self._max = max_entries
        self._items: OrderedDict[tuple[str, str], list[tuple[date, float]]] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_load(self, upstream_key: str, ticker: str, load: Callable[[], list[tuple[date, float]]]) -> list[tuple[date, float]]:
        key = (upstream_key, ticker)
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        points = load()
        with self._lock:
            for stale in [k for k in self._items if k[0] != upstream_key]:
                del self._items[stale]
            self._items[key] = points
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return points
