from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComparisonSummary:
    baseline_r2: float
    full_r2: float
    improvement_pct: float
    thesis_rating: str
    row_count: int
    usable_row_count: int
    withheld_reason: str | None = None
