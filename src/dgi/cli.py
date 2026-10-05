"""Command line entry point. Wiring only: no unit spec (docs/code-conventions.md)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Dividend growth screener over the investment platform API.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Dividend growth screener over the investment platform API."""
