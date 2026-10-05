"""Command line entry point. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import typer

from dgi.client.http import ApiClient
from dgi.pipeline import run_check, run_refresh, run_status
from dgi.report import describe_refresh, describe_status, guard, print_checks
from dgi.settings import Settings

app = typer.Typer(help="Dividend growth screener over the investment platform API.", no_args_is_help=True)


def make_client(settings: Settings) -> ApiClient:
    return ApiClient(settings.api_url, timeout=settings.http_timeout, page_limit=settings.page_limit)


def current_date() -> date:
    return date.today()


def current_time() -> datetime:
    return datetime.now(timezone.utc)


@app.command()
def refresh(
    force: bool = typer.Option(False, "--force", help="Ignore the change keys and rebuild everything."),
    api: str | None = typer.Option(None, "--api", help="Investment API base URL (default: DGI_INVEST_API_URL)."),
) -> None:
    """Pull from the API when upstream changed, build metrics, score, check, and swap the cache in."""
    settings = Settings.from_env()
    if api:
        settings = settings.model_copy(update={"api_url": api})
    with make_client(settings) as client:
        result = guard(lambda: run_refresh(settings, client, today=current_date(), now=current_time(), force=force))
    typer.echo(describe_refresh(result))


@app.command()
def status() -> None:
    """Show what the cache was built from and what it holds."""
    settings = Settings.from_env()
    typer.echo(describe_status(guard(lambda: run_status(settings)), settings.cache_path))


@app.command()
def check() -> None:
    """Run the quality checks on the live cache."""
    settings = Settings.from_env()
    results = guard(lambda: run_check(settings))
    print_checks(results)
    if not all(r.passed for r in results):
        raise typer.Exit(code=1)
