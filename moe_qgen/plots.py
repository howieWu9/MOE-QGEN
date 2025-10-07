"""Plotting utilities for MOE-DUQGEN artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

sns.set_theme(style="whitegrid")


def _load_weights_history(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    records = []
    if not path.exists():
        raise FileNotFoundError(f"weights_history.jsonl not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError(f"No records found in {path}")
    weights = pd.DataFrame(df["weights"].tolist(), columns=["expert_1", "expert_2", "expert_3", "expert_4"])
    weights["step"] = df["step"].values
    weights["cid"] = df["cid"].values
    return weights


def plot_weight_heatmap(history_path: str | Path, output_path: str | Path) -> None:
    df = _load_weights_history(history_path)
    weight_matrix = df.set_index("step")[['expert_1', 'expert_2', 'expert_3', 'expert_4']]
    plt.figure(figsize=(10, 4))
    sns.heatmap(weight_matrix.T, cmap="mako", cbar_kws={"label": "Weight"})
    plt.xlabel("Generation step")
    plt.ylabel("Expert")
    plt.title("Expert weight evolution")
    plt.tight_layout()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=200)
    plt.close()


def plot_cluster_heatmap(history_path: str | Path, cluster_count: int, output_path: str | Path, max_steps: int = 200) -> None:
    df = _load_weights_history(history_path)
    steps = min(max_steps, len(df))
    matrix = np.zeros((cluster_count, steps), dtype=float)
    for idx, cid in enumerate(df["cid"].values[:steps]):
        matrix[int(cid), idx] = 1.0
    plt.figure(figsize=(12, max(4, cluster_count / 10)))
    sns.heatmap(matrix, cmap="crest", cbar=False)
    plt.xlabel("Generation step")
    plt.ylabel("Cluster id")
    plt.title("Cluster visitations (first %s steps)" % steps)
    plt.tight_layout()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=200)
    plt.close()
