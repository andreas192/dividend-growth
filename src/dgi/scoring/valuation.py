from __future__ import annotations

from dataclasses import dataclass

from dgi.scoring.config import ValuationParams


@dataclass(frozen=True)
class FairValue:
    low: float | None
    mid: float | None
    high: float | None


def gordon_value(dps: float, growth: float, required_return: float) -> float | None:
    if dps <= 0 or required_return <= growth:
        return None
    return dps * (1 + growth) / (required_return - growth)


def base_growth(dgr_5: float | None, dgr_3: float | None, params: ValuationParams) -> float | None:
    growth = dgr_5 if dgr_5 is not None else dgr_3
    if growth is None:
        return None
    return min(max(growth, params.growth_floor), params.growth_cap)


def fair_value_range(dps: float | None, growth: float | None, params: ValuationParams) -> FairValue | None:
    if dps is None or growth is None or dps <= 0:
        return None
    r, d = params.required_return, params.range_delta
    low_growth = max(growth - d, params.growth_floor)
    return FairValue(
        low=gordon_value(dps, low_growth, r + d),
        mid=gordon_value(dps, growth, r),
        high=gordon_value(dps, growth, r - d),
    )


def margin_of_safety(fair_mid: float | None, price: float | None) -> float | None:
    if fair_mid is None or price is None or price <= 0:
        return None
    return fair_mid / price - 1


def grid_axes(params: ValuationParams) -> tuple[list[float], list[float]]:
    returns = [round(params.required_return + k * 0.01, 4) for k in (-2, -1, 0, 1, 2)]
    steps = int(round(params.growth_cap / 0.02))
    growths = [round(i * 0.02, 4) for i in range(steps + 1)]
    return returns, growths


def sensitivity_grid(dps: float, returns: list[float], growths: list[float]) -> list[list[float | None]]:
    return [[gordon_value(dps, g, r) for g in growths] for r in returns]
