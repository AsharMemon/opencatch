from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class ValidationRow:
    event_id: str
    year: int
    target: float
    feature_values: dict[str, float]


class ValidationDataset:
    def __init__(self, rows: Sequence[ValidationRow]):
        self.rows = list(rows)

    def filter_years(self, *, min_year: int | None = None, max_year: int | None = None) -> "ValidationDataset":
        rows = [
            row
            for row in self.rows
            if (min_year is None or row.year >= min_year)
            and (max_year is None or row.year <= max_year)
        ]
        return ValidationDataset(rows)

    def feature_names(self) -> list[str]:
        names: set[str] = set()
        for row in self.rows:
            names.update(row.feature_values.keys())
        return sorted(names)

    def to_matrix(self, feature_names: Sequence[str]) -> tuple[list[list[float]], list[float], list[str]]:
        matrix: list[list[float]] = []
        targets: list[float] = []
        event_ids: list[str] = []
        for row in self.rows:
            matrix.append([float(row.feature_values.get(name, 0.0)) for name in feature_names])
            targets.append(float(row.target))
            event_ids.append(row.event_id)
        return matrix, targets, event_ids

    @classmethod
    def from_csv(cls, path: str | Path) -> "ValidationDataset":
        path = Path(path)
        with path.open("r", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"event_id", "year", "target"}
            missing = required.difference(reader.fieldnames or [])
            if missing:
                raise ValueError(f"Missing required columns: {sorted(missing)}")

            rows: list[ValidationRow] = []
            for raw in reader:
                event_id = (raw.get("event_id") or "").strip()
                if not event_id:
                    raise ValueError("event_id cannot be empty")
                year = int(raw["year"])
                target = float(raw["target"])
                feature_values = {
                    key: float(value)
                    for key, value in raw.items()
                    if key not in required and value not in (None, "")
                }
                rows.append(
                    ValidationRow(
                        event_id=event_id,
                        year=year,
                        target=target,
                        feature_values=feature_values,
                    )
                )
        return cls(rows)


def write_template_csv(path: str | Path, rows: Iterable[dict[str, object]]) -> None:
    path = Path(path)
    rows = list(rows)
    if not rows:
        raise ValueError("rows must not be empty")
    fieldnames = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
