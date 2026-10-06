"""The score stage: reads metrics_current and company_dim, writes scores, score_detail and flags."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from dgi.schema import insert_rows, recreate_tables
from dgi.scoring.bands import percentile_rank
from dgi.scoring.config import ScoringConfig, SectorRule, Universe
from dgi.scoring.flags import compute_flags
from dgi.scoring.pillars import score_company
from dgi.scoring.valuation import base_growth, fair_value_range, margin_of_safety

SCORE_TABLES = ("scores", "score_detail", "flags")
SECTOR_OTHER = "Other"
FINANCIAL_REASON = "financial_or_reit"


@dataclass(frozen=True)
class ScoreResult:
    companies: int
    scored: int
    not_scored: dict[str, int]


def sic_number(sic: str | None) -> int | None:
    return int(sic) if sic is not None and sic.strip().isdigit() else None


def in_ranges(value: int | None, ranges: Sequence[tuple[int, int]]) -> bool:
    return value is not None and any(low <= value <= high for low, high in ranges)


def sector_group(sic: str | None, rules: Sequence[SectorRule]) -> str:
    number = sic_number(sic)
    for rule in rules:
        if in_ranges(number, rule.sic):
            return rule.group
    return SECTOR_OTHER


def universe_reason(row: Mapping[str, Any], today: date, universe: Universe) -> str | None:
    """Why a company is not scored, or None when it is a candidate. Checked in this order."""
    if in_ranges(sic_number(row["sic"]), universe.excluded_sic_ranges):
        return FINANCIAL_REASON
    if row["price"] is None:
        return "no_price"
    years = row["years_history"]
    if years is None or years < universe.min_dividend_years:
        return "short_dividend_history"
    fy_end = row["fy_period_end"]
    if fy_end is None or (today - fy_end).days > universe.max_fundamentals_age_days:
        return "insufficient_data"
    return None


def flag_rows(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"ticker": row["ticker"], "code": f.code, "severity": f.severity, "text": f.text} for f in compute_flags(row)]


def load_rows(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cur = con.execute("SELECT c.sic, m.* FROM metrics_current m JOIN company_dim c ON c.ticker = m.ticker ORDER BY m.ticker")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, values)) for values in cur.fetchall()]


def _empty_score(row: Mapping[str, Any], status: str, reason: str | None, group: str) -> dict[str, Any]:
    return {"ticker": row["ticker"], "status": status, "reason": reason, "sector_group": group}


def _percentiles(details: list[dict[str, Any]]) -> None:
    by_metric: dict[str, list[float]] = defaultdict(list)
    for d in details:
        by_metric[d["metric"]].append(d["value"])
    ordered = {metric: sorted(values) for metric, values in by_metric.items()}
    for d in details:
        d["percentile"] = percentile_rank(ordered[d["metric"]], d["value"])


def score_cache(con: duckdb.DuckDBPyConnection, cfg: ScoringConfig, today: date) -> ScoreResult:
    """Replace scores, score_detail and flags from the metrics tables. Never touches the API."""
    scores: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    rows = load_rows(con)
    for row in rows:
        group = sector_group(row["sic"], cfg.sector_groups)
        reason = universe_reason(row, today, cfg.universe)
        if reason != FINANCIAL_REASON:
            flags += flag_rows(row)
        if reason is not None:
            reasons[reason] += 1
            scores.append(_empty_score(row, "not_scored", reason, group))
            continue
        growth = base_growth(row["dgr_5"], row["dgr_3"], cfg.valuation)
        fair = fair_value_range(row["dividend_ttm"], growth, cfg.valuation)
        safety_margin = margin_of_safety(fair.mid if fair else None, row["price"])
        values = {metric: row.get(metric) for metric in cfg.bands if metric != "margin_of_safety"}
        values["margin_of_safety"] = safety_margin
        result = score_company(values, cfg)
        if result.total is None or result.coverage < cfg.universe.min_coverage:
            reasons["insufficient_data"] += 1
            scores.append(_empty_score(row, "not_scored", "insufficient_data", group))
            continue
        scores.append({
            **_empty_score(row, "scored", None, group), **result.pillars, "coverage": result.coverage, "total": result.total,
            "fair_value_low": fair.low if fair else None, "fair_value_mid": fair.mid if fair else None,
            "fair_value_high": fair.high if fair else None, "margin_of_safety": safety_margin,
        })
        details += [{"ticker": row["ticker"], "metric": d.metric, "pillar": d.pillar, "value": d.value, "band_score": d.band_score,
                     "weight": d.weight, "contribution": d.contribution} for d in result.details]
    _percentiles(details)
    recreate_tables(con, SCORE_TABLES)
    insert_rows(con, "scores", scores)
    insert_rows(con, "score_detail", details)
    insert_rows(con, "flags", flags)
    scored = sum(1 for s in scores if s["status"] == "scored")
    return ScoreResult(len(rows), scored, dict(reasons))
