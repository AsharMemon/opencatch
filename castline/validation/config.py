from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = ROOT / 'castline' / 'validation' / 'data' / 'raw'
PROCESSED_DATA_DIR = ROOT / 'castline' / 'validation' / 'data' / 'processed'
ARTIFACTS_DIR = ROOT / 'castline' / 'validation' / 'artifacts'


@dataclass(frozen=True)
class ValidationPaths:
    raw_data: Path = RAW_DATA_DIR
    processed_data: Path = PROCESSED_DATA_DIR
    artifacts: Path = ARTIFACTS_DIR

    def ensure(self) -> None:
        self.raw_data.mkdir(parents=True, exist_ok=True)
        self.processed_data.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
