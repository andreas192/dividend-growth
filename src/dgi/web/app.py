"""Routes of the read-only UI. Handlers are thin: parse, query the cache, render. All logic lives in `dgi.cache` and `dgi.web.series`."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from dgi.cache import (
    BadQuery, CacheHandle, CacheMeta, ScoringConfig, export_rows, fair_value_range, grid_axes, load_annual, load_company, load_payments,
    parse_query, query_screener, query_unscored, read_meta_from, read_status_from, scatter_points, sector_groups, sensitivity_grid,
    universe_stats, PILLARS,
)
from dgi.errors import DgiError
from dgi.settings import Settings
from dgi.web import format as fmt
from dgi.web.series import PriceSource, SeriesCache, annual_charts, daily_charts

HERE = Path(__file__).parent
TICKER = re.compile(r"[A-Za-z0-9.\-]{1,10}")
RATIO_CHARTS = {"payout", "leverage", "coverage"}  # not shown for banks, insurers and REITs: those ratios do not describe them
CSV_COLUMNS = [
    "rank", "ticker", "name", "sector", "price", "yield_pct", "streak", "dgr_5_pct", "payout_fcf_pct", "payout_eps_pct", "market_cap",
    "dividend_score", "safety_score", "growth_score", "valuation_score", "score", "margin_of_safety_pct", "red_flags",
]


def _times100(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 4)


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    env = templates.env
    env.filters.update(
        pct=fmt.pct, num=fmt.num, money=fmt.money, big=fmt.big, score=fmt.score, metric_label=fmt.metric_label,
        metric_value=fmt.metric_value, reason_label=fmt.reason_label, streak_label=fmt.streak_label,
    )
    env.globals.update(pillar_labels=fmt.PILLAR_LABELS, pillars=PILLARS, dash=fmt.DASH)
    env.globals["qs"] = lambda params, **overrides: urlencode({**params, **{k: str(v) for k, v in overrides.items()}})
    return templates


def footer(meta: CacheMeta | None, scoring_hash: str | None = None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {"built_at": meta.built_at[:16].replace("T", " "), "upstream_built_at": (meta.upstream_built_at or "unknown")[:10],
            "contract_version": meta.contract_version, "contract_warnings": meta.contract_warnings,
            "scoring_drift": scoring_hash is not None and scoring_hash != meta.scoring_hash}


def render(request: Request, name: str, context: dict[str, Any], status: int = 200) -> Response:
    return request.app.state.templates.TemplateResponse(request, name, context, status_code=status)


def no_cache(request: Request) -> Response:
    return render(request, "no_cache.html", {"footer": None}, status=503)


def not_found(request: Request, exc: Exception) -> Response:
    return render(request, "error.html", {"footer": None, "title": "Not found", "message": "There is nothing at this address."}, status=404)


def bad_query(request: Request, exc: BadQuery) -> Response:
    return render(request, "error.html", {"footer": None, "title": "Check the filters", "message": f"{exc.param}: {exc.reason}"}, status=422)


def screener(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        if con is None:
            return no_cache(request)
        q = parse_query(request.query_params, state.cfg.hard_filters, state.cfg.pillars)
        stats = universe_stats(con, q)
        page, unscored, unscored_total, unscored_pages = None, [], 0, 1
        if q.show_unscored:
            unscored, unscored_total = query_unscored(con, q)
            unscored_pages = max(1, -(-unscored_total // q.size))
        else:
            page = query_screener(con, q)
        context = {"q": q, "stats": stats, "page": page, "unscored": unscored, "unscored_total": unscored_total,
                   "unscored_page": min(q.page, unscored_pages), "unscored_pages": unscored_pages,
                   "sectors": sector_groups(con), "footer": footer(read_meta_from(con), state.scoring_hash)}
    return render(request, "screener.html", context)


def screener_csv(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        if con is None:
            return no_cache(request)
        rows = export_rows(con, parse_query(request.query_params, state.cfg.hard_filters, state.cfg.pillars))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([
            r.position, fmt.csv_safe(r.ticker), fmt.csv_safe(r.name), fmt.csv_safe(r.sector), r.price, _times100(r.div_yield), r.streak, _times100(r.dgr_5),
            _times100(r.payout_fcf), _times100(r.payout_earnings), r.market_cap, r.dividend, r.safety, r.growth, r.valuation, r.score,
            _times100(r.margin_of_safety), " ".join(r.red_flags),
        ])
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="dgi-screener.csv"'})


def scatter(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        if con is None:
            return JSONResponse({"error": "no cache"}, status_code=503)
        points = scatter_points(con, parse_query(request.query_params, state.cfg.hard_filters, state.cfg.pillars))
    return JSONResponse({"points": [
        {"ticker": p.ticker, "name": p.name, "yield": _times100(p.div_yield), "dgr5": _times100(p.dgr_5), "score": p.score} for p in points
    ]})


def _ticker(request: Request) -> str:
    ticker = request.path_params["ticker"]
    if not TICKER.fullmatch(ticker):
        raise HTTPException(404)
    return ticker


def valuation_panel(view: Any, cfg: ScoringConfig) -> dict[str, Any] | None:
    dps = view.metrics.get("dividend_ttm")
    if not dps or dps <= 0:
        return None
    returns, growths = grid_axes(cfg.valuation)
    return {"returns": returns, "growths": growths, "grid": sensitivity_grid(dps, returns, growths), "dps": dps,
            "price": view.metrics.get("price")}


def company(request: Request) -> Response:
    state = request.app.state
    ticker = _ticker(request)
    with state.handle.connection() as con:
        if con is None:
            return no_cache(request)
        view = load_company(con, ticker)
        if view is None:
            raise HTTPException(404)
        annual = load_annual(con, view.ticker)
        meta = read_meta_from(con)
    show_ratios = view.reason != "financial_or_reit"
    charts = [c for c in annual_charts(annual, view.metrics.get("div_yield_avg_5y")) if show_ratios or c.id not in RATIO_CHARTS]
    context = {"c": view, "charts": [c.to_json() for c in charts], "show_ratios": show_ratios,
               "valuation": valuation_panel(view, state.cfg) if show_ratios else None, "footer": footer(meta, state.scoring_hash)}
    return render(request, "company.html", context)


def daily(request: Request) -> Response:
    state = request.app.state
    ticker = _ticker(request)
    with state.handle.connection() as con:
        if con is None:
            return JSONResponse({"available": False, "notice": "There is no cache yet."}, status_code=503)
        view = load_company(con, ticker)
        if view is None:
            raise HTTPException(404)
        payments = load_payments(con, view.ticker)
        meta = read_meta_from(con)
    average = view.metrics.get("div_yield_avg_5y")
    if state.price_source is None:
        return JSONResponse({"available": False, "notice": "Daily prices are not configured; showing annual figures."})
    try:
        points = state.series_cache.get_or_load(meta.upstream_key if meta else "", view.ticker, lambda: state.price_source.daily(view.ticker))
    except DgiError:
        return JSONResponse({"available": False, "notice": "Daily prices are unavailable right now; showing annual data only."})
    return JSONResponse({"available": True, "charts": [c.to_json() for c in daily_charts(points, payments, average)]})


def methodology(request: Request) -> Response:
    state = request.app.state
    with state.handle.connection() as con:
        meta = None if con is None else read_meta_from(con)
    return render(request, "methodology.html", {"cfg": state.cfg, "footer": footer(meta, state.scoring_hash)})


def health(request: Request) -> Response:
    with request.app.state.handle.connection() as con:
        if con is None:
            return JSONResponse({"status": "no_cache"})
        status = read_status_from(con)
    m = status.meta
    return JSONResponse({
        "status": "ok", "built_at": m.built_at, "upstream_key": m.upstream_key, "upstream_built_at": m.upstream_built_at,
        "contract_version": m.contract_version, "contract_warnings": m.contract_warnings, "metrics_version": m.metrics_version,
        "counts": status.counts, "scored": status.scored, "not_scored": status.not_scored,
    })


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    yield
    app.state.handle.close()


def create_app(settings: Settings, cfg: ScoringConfig, price_source: PriceSource | None = None, scoring_hash: str | None = None) -> Starlette:
    app = Starlette(
        routes=[
            Route("/", screener), Route("/screener.csv", screener_csv), Route("/api/scatter", scatter),
            Route("/company/{ticker}", company), Route("/api/company/{ticker}/daily", daily),
            Route("/methodology", methodology), Route("/health", health),
            Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
        ],
        exception_handlers={404: not_found, BadQuery: bad_query},
        middleware=[Middleware(GZipMiddleware, minimum_size=1000)],  # a long company's daily series is close to 1 MB of JSON
        lifespan=lifespan,
    )
    app.state.cfg = cfg
    app.state.scoring_hash = scoring_hash  # of the file `cfg` was loaded from; None skips the drift notice
    app.state.handle = CacheHandle(settings.cache_path)
    app.state.templates = make_templates()
    app.state.price_source = price_source
    app.state.series_cache = SeriesCache()
    return app
