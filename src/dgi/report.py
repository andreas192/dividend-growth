"""CLI output formatting and the error-to-exit-code guard. No business logic."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import typer

from dgi.errors import DgiError

T = TypeVar("T")


def guard(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except DgiError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
