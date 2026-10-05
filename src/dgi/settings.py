from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

API_VERSION = "v1"

_ENV_NAMES = {
    "api_url": "DGI_INVEST_API_URL",
    "data_dir": "DGI_DATA_DIR",
    "scoring_path": "DGI_SCORING_CONFIG",
    "host": "DGI_HOST",
    "port": "DGI_PORT",
    "page_limit": "DGI_PAGE_LIMIT",
}


class Settings(BaseModel):
    api_url: str = "http://127.0.0.1:8750"
    data_dir: Path = Path("data")
    scoring_path: Path = Path("config/scoring.yaml")
    host: str = "127.0.0.1"
    port: int = 8760
    page_limit: int = 100_000
    http_timeout: float = 120.0

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "dgi.duckdb"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        return cls(**{field: source[name] for field, name in _ENV_NAMES.items() if name in source})
