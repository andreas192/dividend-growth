"""Command line entry point. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import typer
import uvicorn
from starlette.applications import Starlette

from dgi.client.http import ApiClient
from dgi.pipeline import build_web_app, run_check, run_refresh, run_status
from dgi.report import describe_refresh, describe_status, exposure_warning, guard, print_checks
from dgi.settings import Settings

app = typer.Typer(help="Dividend growth screener over the investment platform API.", no_args_is_help=True)


def make_client(settings: Settings) -> ApiClient:
    return ApiClient(settings.api_url, timeout=settings.http_timeout, page_limit=settings.page_limit)


def current_date() -> date:
    return date.today()


def current_time() -> datetime:
    return datetime.now(timezone.utc)


def run_server(web_app: Starlette, host: str, port: int) -> None:
    uvicorn.run(web_app, host=host, port=port, log_level="info")


@app.command()
def refresh(
    force: bool = typer.Option(False, "--force", help="Ignore the change keys and rebuild everything."),
    api: str | None = typer.Option(None, "--api", help="Investment API base URL (default: DGI_INVEST_API_URL)."),
) -> None:
    """Pull from the API when upstream changed, build metrics, score, check, and swap the cache in."""
    settings = guard(Settings.from_env)
    if api:
        settings = settings.model_copy(update={"api_url": api})
    with make_client(settings) as client:
        result = guard(lambda: run_refresh(settings, client, today=current_date(), now=current_time(), force=force))
    typer.echo(describe_refresh(result))


@app.command()
def status() -> None:
    """Show what the cache was built from and what it holds."""
    settings = guard(Settings.from_env)
    typer.echo(describe_status(guard(lambda: run_status(settings)), settings.cache_path))


@app.command()
def check() -> None:
    """Run the quality checks on the live cache."""
    settings = guard(Settings.from_env)
    results = guard(lambda: run_check(settings))
    print_checks(results)
    if not all(r.passed for r in results):
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host", help="Address to listen on (default: DGI_HOST or 127.0.0.1)."),
    port: int | None = typer.Option(None, "--port", help="Port (default: DGI_PORT or 8760)."),
) -> None:
    """Serve the read-only web UI over the cache."""
    settings = guard(Settings.from_env)
    settings = settings.model_copy(update={k: v for k, v in (("host", host), ("port", port)) if v is not None})
    warning = exposure_warning(settings.host)
    if warning:
        typer.echo(warning, err=True)
    with make_client(settings) as client:
        web_app = guard(lambda: build_web_app(settings, client))
        run_server(web_app, settings.host, settings.port)
