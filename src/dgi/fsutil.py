from __future__ import annotations

import hashlib
import os
from pathlib import Path


def atomic_replace(src: Path, dst: Path) -> None:
    """Rename src over dst (same filesystem), so readers see the old or the new file, never a mix."""
    os.replace(src, dst)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
