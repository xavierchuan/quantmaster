#!/usr/bin/env python
"""
Combine walk-forward backtest outputs across multiple sleeves using a weight file.

For each config, the latest walk-forward session directory is detected, its
metrics.csv is read, and per-window returns are computed. These returns are
aggregated using the provided sleeve weights to produce a portfolio equity
curve plus summary metrics.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from loguru import logger

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from metrics.perf import compute_metrics  # type: ignore

BARs_PER_YEAR = 24 * 252
BASE_DIR = Path(__file__).resolve().parents[1]


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


def load_weights(path: Path) -> Dict[str, float]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    if not isinstance(data, dict):
        raise ValueError("Weight file must contain mapping of sleeve -> weight")
    total = sum(float(v) for v in data.values())
    if total == 0:
        raise ValueError("Weight file sum is zero")
    return {k: float(v) / total for k, v in data.items()}


def find_latest_walkforward(root: Path, slug: str) -> Path:
    pattern = f"walkforward_{slug}_*"
    candidates = sorted(root.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No walk-forward sessions matching {pattern} under {root}")
    # directories already include timestamp suffix; choose last lexicographically
    return candidates[-1] / "walkforward"


def load_config_cash(config_path: Path) -> float:
    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cash = data.get("cash")
    if cash is None:
        raise ValueError(f"Config {config_path} missing 'cash' for initial capital")
    return float(cash)


def load_window_returns(metrics_path: Path, initial_cash: float) -> List[np.ndarray]:
    df = pd.read_csv(metrics_path).sort_values("window")
    series: List[np.ndarray] = []
    for _, row in df.iterrows():
        equity_path = row.get("equity_path")
        arr: Optional[np.ndarray] = None
        if isinstance(equity_path, str) and equity_path:
            src = _resolve_path(equity_path)
            if src.exists():
                eq_df = pd.read_csv(src)
                equities = eq_df["equity"].astype(float).to_numpy()
                if equities.size >= 2:
                    arr = equities[1:] / equities[:-1] - 1.0
        if arr is None:
            bars = int(row.get("test_rows", 0)) or 1
            final_equity = float(row.get("final_equity", initial_cash))
            total_return = final_equity / initial_cash - 1.0
            bar_return = (1.0 + total_return) ** (1.0 / bars) - 1.0
            arr = np.full(bars, bar_return, dtype=float)
        series.append(arr)
    return series


def build_portfolio_equity(
    sleeve_returns: Dict[str, List[np.ndarray]],
    weights: Dict[str, float],
) -> Tuple[List[Tuple[int, float]], List[float]]:
    labels = list(weights.keys())
    min_windows = min(len(sleeve_returns[label]) for label in labels)
    equity = 1.0
    equity_series: List[Tuple[int, float]] = []
    per_window_returns: List[float] = []
    bar_index = 0
    for win_idx in range(min_windows):
        eq_start = equity
        window_arrays = {label: sleeve_returns[label][win_idx] for label in labels}
        min_len = min(len(arr) for arr in window_arrays.values())
        for step in range(min_len):
            bar_return = sum(weights[label] * window_arrays[label][step] for label in labels)
            equity *= 1.0 + bar_return
            equity_series.append((bar_index, equity))
            bar_index += 1
        per_window_returns.append(equity / eq_start - 1.0)
    return equity_series, per_window_returns


def save_summary(
    out_path: Path,
    weights: Dict[str, float],
    equity_series: List[Tuple[int, float]],
    per_window_returns: List[float],
    meta: Dict[str, str],
) -> None:
    metrics = compute_metrics(equity_series, bars_per_year=BARs_PER_YEAR)
    summary = {
        "timestamp": datetime.utcnow().isoformat(),
        "weights": weights,
        "metrics": metrics,
        "per_window_returns": per_window_returns,
        "meta": meta,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    logger.info("Saved walk-forward portfolio summary to %s", out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate walk-forward outputs into a portfolio summary.")
    parser.add_argument("--configs", nargs="+", required=True, help="List of YAML configs used in walk-forward runs.")
    parser.add_argument("--weights", required=True, help="JSON file containing sleeve weights.")
    parser.add_argument(
        "--walkforward-root",
        default="QuantResearch/results/walkforward_fx_top6",
        help="Root directory containing walkforward_<slug>_* subdirectories.",
    )
    parser.add_argument("--output", default="QuantResearch/results/walkforward_portfolio_summary.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    weight_map = load_weights(Path(args.weights).expanduser().resolve())
    wf_root = Path(args.walkforward_root).expanduser().resolve()
    sleeve_returns: Dict[str, List[np.ndarray]] = {}
    meta_info = {}
    for cfg_path_str in args.configs:
        cfg_path = Path(cfg_path_str).expanduser().resolve()
        slug = cfg_path.stem
        # Map weight key: assume weight file uses config filename
        if slug in weight_map:
            weight_key = slug
        else:
            weight_key = cfg_path.name
        if weight_key not in weight_map:
            logger.warning("Weight file missing entry for %s; assigning zero weight", weight_key)
            weight_map[weight_key] = 0.0
        wf_dir = find_latest_walkforward(wf_root, slug)
        metrics_path = wf_dir / "metrics.csv"
        init_cash = load_config_cash(cfg_path)
        series = load_window_returns(metrics_path, init_cash)
        sleeve_returns[weight_key] = series
        meta_info[weight_key] = str(metrics_path)
    # ensure weights normalized to used keys
    filtered_weights = {k: weight_map.get(k, 0.0) for k in sleeve_returns.keys()}
    total = sum(filtered_weights.values())
    if total == 0:
        raise ValueError("All relevant weights are zero")
    filtered_weights = {k: v / total for k, v in filtered_weights.items()}
    equity_series, per_window_returns = build_portfolio_equity(sleeve_returns, filtered_weights)
    save_summary(Path(args.output).expanduser().resolve(), filtered_weights, equity_series, per_window_returns, meta_info)


if __name__ == "__main__":
    main()
