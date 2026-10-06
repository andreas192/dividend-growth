"""Typed errors. The CLI's guard turns any DgiError into `error: ...` on stderr and exit code 1."""


class DgiError(RuntimeError):
    pass


class ConfigError(DgiError):
    """config/scoring.yaml is missing or invalid."""


class ApiUnavailable(DgiError):
    """The investment API cannot be reached or answers 503 (gold drift or missing)."""


class ApiError(DgiError):
    """The investment API answered something this client does not expect (4xx, bad body)."""


class ContractError(DgiError):
    """The API contract is retired, or a required resource or column is missing or retyped."""


class CacheCheckError(DgiError):
    """A newly built cache failed its quality checks; the old cache keeps serving."""


class CacheMissing(DgiError):
    """There is no cache file yet (or it cannot be opened); run `dgi refresh`."""
