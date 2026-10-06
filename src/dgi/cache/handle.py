"""A read-only connection to the live cache that follows atomic swaps."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from dgi.errors import STORAGE_ERRORS, CacheMissing


@contextmanager
def open_readonly(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection for the CLI. A missing, unopenable or unreadable file is `CacheMissing` with a way out; a SQL bug is not caught."""
    if not path.exists():
        raise CacheMissing(f"no cache at {path}; run `dgi refresh`")
    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:  # opening runs no SQL of ours: any failure is about the file
        raise CacheMissing(f"cannot open the cache at {path}: {exc}; run `dgi refresh --force`") from exc
    try:
        yield con
    except STORAGE_ERRORS as exc:
        raise CacheMissing(f"the cache at {path} is unreadable: {exc}; run `dgi refresh --force`") from exc
    finally:
        con.close()


class CacheHandle:
    """Opens the cache read-only and reopens it when the file is replaced.

    DuckDB keeps one instance per path inside a process, so after a swap a plain `connect()` would keep
    serving the old file: the old connection is closed first. One lock serializes readers; the app has one user.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._stamp: tuple[int, int] | None = None

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            return None
        return st.st_ino, st.st_mtime_ns

    def _refresh(self) -> None:
        stamp = self._stat()
        if stamp == self._stamp and (self._con is not None or stamp is None):
            return
        if self._con is not None:
            self._con.close()
            self._con = None
        self._stamp = stamp
        if stamp is not None:
            try:
                self._con = duckdb.connect(str(self._path), read_only=True)
            except duckdb.Error:
                self._con = None  # unreadable: served as "no cache" until the next swap

    @contextmanager
    def connection(self) -> Iterator[duckdb.DuckDBPyConnection | None]:
        """The current connection, or None when there is no usable cache."""
        with self._lock:
            self._refresh()
            yield self._con

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
            self._stamp = None
