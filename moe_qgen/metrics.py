"""Metric aggregation helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd


@dataclass
class MetricsSummary:
    dataset: str
    experiment: str
    select_k_time: float | None = None
    select_k_memory: float | None = None
    avg_r1: float | None = None
    uniqueness: float | None = None
    coverage: float | None = None
    ndcg10: float | None = None
    map: float | None = None
    mrr: float | None = None
    extras: Dict[str, float | None] = field(default_factory=dict)

    def to_row(self) -> Dict[str, float | None]:
        base = {
            "dataset": self.dataset,
            "experiment": self.experiment,
            "select_k_time": self.select_k_time,
            "select_k_memory": self.select_k_memory,
            "avg_r1": self.avg_r1,
            "uniqueness": self.uniqueness,
            "coverage": self.coverage,
            "ndcg@10": self.ndcg10,
            "map": self.map,
            "mrr": self.mrr,
        }
        base.update(self.extras)
        return base


def load_metrics_files(paths: Iterable[str]) -> Dict[str, float]:
    aggregated: Dict[str, float] = {}
    for path in paths:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Metrics file not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, Mapping):
            raise ValueError(f"Metrics file must contain a JSON object: {p}")
        aggregated.update({k: float(v) for k, v in data.items() if isinstance(v, (int, float))})
    return aggregated


def aggregate_metrics(summaries: Iterable[MetricsSummary]) -> pd.DataFrame:
    rows = [summary.to_row() for summary in summaries]
    return pd.DataFrame(rows)
