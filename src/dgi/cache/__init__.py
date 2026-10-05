"""The derived cache: meta and refresh planning, atomic build and swap, quality checks, read-only queries.

The web app imports only this package; the names it needs from `scoring` are re-exported here.
"""

from dgi.cache.handle import CacheHandle
from dgi.cache.meta import CacheMeta, read_meta_from
from dgi.cache.screener import (
    BadQuery, ScatterPoint, ScreenerPage, ScreenerQuery, ScreenerRow, UniverseStats, UnscoredRow, export_rows, parse_query,
    query_screener, query_unscored, scatter_points, sector_groups, universe_stats,
)
from dgi.cache.status import CacheStatus, read_status_from
from dgi.scoring.config import PILLARS, HardFilters, ScoringConfig, load_scoring_config
from dgi.scoring.valuation import FairValue, fair_value_range, grid_axes, sensitivity_grid

__all__ = [
    "BadQuery", "CacheHandle", "CacheMeta", "CacheStatus", "FairValue", "HardFilters", "PILLARS", "ScatterPoint",
    "ScoringConfig", "ScreenerPage", "ScreenerQuery", "ScreenerRow", "UniverseStats", "UnscoredRow", "export_rows",
    "fair_value_range", "grid_axes", "load_scoring_config", "parse_query",
    "query_screener", "query_unscored", "read_meta_from", "read_status_from", "scatter_points", "sector_groups", "sensitivity_grid", "universe_stats",
]
