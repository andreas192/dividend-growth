"""Settings that change how history is read. They live in the `metrics:` section of config/scoring.yaml."""

from __future__ import annotations

import json

from pydantic import BaseModel

from dgi.fsutil import text_sha256


class MetricParams(BaseModel):
    raise_tolerance: float = 0.001   # a raise is more than this fraction above the prior year
    cut_tolerance: float = 0.01      # a cut is more than this fraction below the prior year
    special_ratio: float = 1.5       # an extra payment this many times the median regular one is special
    ttm_min_span_days: int = 240     # four quarters span 240-300 days between first and last period end
    ttm_max_span_days: int = 300
    payout_cap: float = 9.99         # payout ratios are capped at 999%
    ratio_cap: float = 99.0          # coverage and leverage ratios are capped here


def params_hash(params: MetricParams) -> str:
    return text_sha256(json.dumps(params.model_dump(), sort_keys=True))
