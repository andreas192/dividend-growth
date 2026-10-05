from __future__ import annotations

from bisect import bisect_right


def band_score(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    i = bisect_right([p[0] for p in points], x)
    (x0, y0), (x1, y1) = points[i - 1], points[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def percentile_rank(sorted_values: list[float], x: float) -> float:
    return 100.0 * bisect_right(sorted_values, x) / len(sorted_values)
