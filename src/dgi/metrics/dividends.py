from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from itertools import groupby

import duckdb
import pyarrow as pa


@dataclass(frozen=True)
class DividendMetrics:
    streak: int
    no_cut_streak: int
    dgr_1: float | None
    dgr_3: float | None
    dgr_5: float | None
    dgr_10: float | None
    years_history: int
    payment_frequency: int | None
    cut_years_5y: int
    irregular_payments: bool


def _step(series: Mapping[int, float], year: int, raise_tol: float, cut_tol: float) -> str:
    cur, prev = series.get(year), series.get(year - 1)
    if cur is None or prev is None:
        return "gap"
    if cur > prev * (1 + raise_tol):
        return "raise"
    if cur < prev * (1 - cut_tol):
        return "cut"
    return "flat"


def compute_streaks(series: Mapping[int, float], last_year: int, raise_tol: float, cut_tol: float) -> tuple[int, int]:
    if not series:
        return 0, 0
    increases = no_cuts = 0
    increasing = not_cutting = True
    for year in range(last_year, min(series), -1):
        kind = _step(series, year, raise_tol, cut_tol)
        increasing = increasing and kind == "raise"
        not_cutting = not_cutting and kind in ("raise", "flat")
        if not (increasing or not_cutting):
            break
        increases += increasing
        no_cuts += not_cutting
    return increases, no_cuts


def dividend_cagr(series: Mapping[int, float], last_year: int, years: int) -> float | None:
    first, last = series.get(last_year - years), series.get(last_year)
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def count_cut_years(series: Mapping[int, float], last_year: int, window: int, raise_tol: float, cut_tol: float) -> int:
    cuts = 0
    for year in range(last_year - window + 1, last_year + 1):
        kind = _step(series, year, raise_tol, cut_tol)
        suspended = kind == "gap" and series.get(year) is None and series.get(year - 1) is not None
        cuts += kind == "cut" or suspended
    return cuts


def usual_count(counts: Mapping[int, int]) -> int | None:
    if not counts:
        return None
    tally: dict[int, int] = {}
    for n in counts.values():
        tally[n] = tally.get(n, 0) + 1
    return min(tally, key=lambda n: (-tally[n], n))


def compute_dividend_metrics(
    series: Mapping[int, float], counts: Mapping[int, int], last_year: int, raise_tol: float, cut_tol: float
) -> DividendMetrics:
    streak, no_cut = compute_streaks(series, last_year, raise_tol, cut_tol)
    last_count = counts.get(last_year)
    return DividendMetrics(
        streak=streak,
        no_cut_streak=no_cut,
        dgr_1=dividend_cagr(series, last_year, 1),
        dgr_3=dividend_cagr(series, last_year, 3),
        dgr_5=dividend_cagr(series, last_year, 5),
        dgr_10=dividend_cagr(series, last_year, 10),
        years_history=len(series),
        payment_frequency=last_count,
        cut_years_5y=count_cut_years(series, last_year, 5, raise_tol, cut_tol),
        irregular_payments=last_count is not None and last_count != usual_count(counts),
    )


METRICS_SCHEMA = pa.schema([
    ("ticker", pa.string()), ("streak", pa.int32()), ("no_cut_streak", pa.int32()),
    ("dgr_1", pa.float64()), ("dgr_3", pa.float64()), ("dgr_5", pa.float64()), ("dgr_10", pa.float64()),
    ("years_history", pa.int32()), ("payment_frequency", pa.int32()), ("cut_years_5y", pa.int32()),
    ("irregular_payments", pa.bool_()),
])


def build_dividend_metrics(con: duckdb.DuckDBPyConnection, today: date, raise_tol: float, cut_tol: float) -> int:
    rows = con.execute(
        "SELECT ticker, year, dps, n_payments FROM dividend_annual WHERE complete ORDER BY ticker, year"
    ).fetchall()
    last_year = today.year - 1
    records: list[dict] = []
    for ticker, group in groupby(rows, key=lambda r: r[0]):
        group = list(group)
        series = {year: dps for _, year, dps, _ in group}
        counts = {year: n for _, year, _, n in group}
        metrics = compute_dividend_metrics(series, counts, last_year, raise_tol, cut_tol)
        records.append({"ticker": ticker, **metrics.__dict__})
    table = pa.Table.from_pylist(records, schema=METRICS_SCHEMA)
    con.register("dividend_metric_rows", table)
    con.execute("CREATE OR REPLACE TEMP TABLE tmp_dividend_metrics AS SELECT * FROM dividend_metric_rows")
    con.unregister("dividend_metric_rows")
    return len(records)
