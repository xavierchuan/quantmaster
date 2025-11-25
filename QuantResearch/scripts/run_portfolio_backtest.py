#!/usr/bin/env python
"""
Run multiple single-asset configs, aggregate their equity curves, and report
portfolio-level performance/correlation stats.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
from loguru import logger

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

from core.backtest.strategy_engine import parse_strategy_specs  # type: ignore
from metrics.perf import compute_metrics  # type: ignore
from scripts.backtest_strategy import BASE_DIR, run_once  # type: ignore
from scripts.run_batch_backtests import load_params  # type: ignore


def _prepare_job(cfg_path: Path) -> Tuple[str, Dict, Dict]:
    params = load_params(cfg_path)
    config_name = params.pop("config_name", cfg_path.name)
    label = params.get("label") or config_name or cfg_path.stem
    job_params = {k: v for k, v in params.items() if k not in ("label", "config_name")}
    csv_path = job_params.get("csv_path")
    if csv_path:
        job_params["csv_path"] = str(Path(csv_path).expanduser().resolve())
    if isinstance(job_params.get("strategies"), (list, dict)):
        job_params["strategies"] = parse_strategy_specs(job_params["strategies"])
    return label, job_params, {"config_name": config_name, "raw_params": params}


def _load_summary(summary_path: Path) -> dict:
    with summary_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_equity_series(rel_path: str) -> pd.Series:
    eq_path = (Path(BASE_DIR) / rel_path).resolve()
    if not eq_path.exists():
        raise FileNotFoundError(f"Equity curve not found: {eq_path}")
    df = pd.read_csv(eq_path, parse_dates=["ts"])
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts")
    series = df.set_index("ts")["equity"].astype(float)
    return series


def _load_weights(path: Path) -> Dict[str, float]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "weights" in data:
        weights = data["weights"]
    else:
        weights = data
    if not isinstance(weights, dict):
        raise ValueError("Weight file must contain a mapping of sleeve -> weight")
    return {str(k): float(v) for k, v in weights.items()}


def _combine_equity(series_map: Dict[str, pd.Series], weights: Optional[Dict[str, float]] = None) -> Tuple[pd.DataFrame, Dict[str, float]]:
    if not series_map:
        raise ValueError("No equity series available for aggregation")
    union_index = None
    for ser in series_map.values():
        union_index = ser.index if union_index is None else union_index.union(ser.index)
    union_index = union_index.sort_values()  # type: ignore
    df = pd.DataFrame(index=union_index)
    starts: Dict[str, float] = {}
    for label, ser in series_map.items():
        df[label] = ser.reindex(union_index).ffill().bfill()
        starts[label] = float(ser.iloc[0])
    labels = list(series_map.keys())
    if weights:
        normalized = {k: weights.get(k, 0.0) for k in labels}
        total = sum(normalized.values())
        if total == 0:
            raise ValueError("Weight file produced zero total weight")
        normalized = {k: v / total for k, v in normalized.items()}
        total_initial = sum(normalized[k] * starts[k] for k in labels)
        normalized_equity = pd.DataFrame(
            {k: df[k] / starts[k] for k in labels},
            index=df.index,
        )
        weighted_series = sum(normalized[k] * normalized_equity[k] for k in labels)
        df["portfolio_equity"] = total_initial * weighted_series
    else:
        df["portfolio_equity"] = df[labels].sum(axis=1)
        normalized = {k: 1.0 / len(labels) for k in labels}
    return df, normalized


def _write_outputs(
    out_dir: Path,
    tag: str,
    runs: List[dict],
    equity_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    corr_df: pd.DataFrame,
    weights: Dict[str, float],
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    eq_path = out_dir / "portfolio_equity.csv"
    equity_df.reset_index().rename(columns={"index": "ts"}).to_csv(eq_path, index=False)
    corr_path = out_dir / "portfolio_correlation.csv"
    corr_df.to_csv(corr_path)
    sleeve_metrics_path = out_dir / "sleeve_metrics.csv"
    sleeve_df = pd.DataFrame(
        [
            {
                "label": run["label"],
                "symbol": run["symbol"],
                "config_path": run["config_path"],
                "run_id": run["run_id"],
                "sharpe": run["metrics"].get("sharpe"),
                "ann_return": run["metrics"].get("ann_return"),
                "ann_vol": run["metrics"].get("ann_vol"),
                "max_drawdown": run["metrics"].get("max_drawdown"),
                "trades": run["metrics"].get("trades"),
                "final_equity": run["metrics"].get("final_equity"),
                "summary_path": run["summary_path"],
            }
            for run in runs
        ]
    )
    sleeve_df.to_csv(sleeve_metrics_path, index=False)

    portfolio_metrics = compute_metrics(
        list(
            zip(
                equity_df.index.to_pydatetime(),  # type: ignore[attr-defined]
                equity_df["portfolio_equity"].astype(float).tolist(),
            )
        ),
        bars_per_year=24 * 252,
    )
    portfolio_metrics["initial_equity"] = float(equity_df["portfolio_equity"].iloc[0])
    portfolio_metrics["final_equity"] = float(equity_df["portfolio_equity"].iloc[-1])

    summary_doc = {
        "tag": tag,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio_metrics": portfolio_metrics,
        "portfolio_equity_csv": str(eq_path.relative_to(Path(BASE_DIR))),
        "correlation_csv": str(corr_path.relative_to(Path(BASE_DIR))),
        "sleeve_metrics_csv": str(sleeve_metrics_path.relative_to(Path(BASE_DIR))),
        "sleeves": runs,
        "weights": weights,
    }
    summary_path = out_dir / "portfolio_summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary_doc, fh, indent=2, ensure_ascii=False)
    return summary_path


def run_portfolio(configs: Sequence[Path], tag: str, out_root: Path, weight_map: Optional[Dict[str, float]] = None) -> Path:
    runs: List[dict] = []
    equity_series: Dict[str, pd.Series] = {}

    for cfg in configs:
        label, job_params, meta = _prepare_job(cfg)
        logger.info(f"[Portfolio] Running sleeve {label} ({cfg})")
        result = run_once(**job_params)
        summary_path = Path(result["summary_path"]).resolve()
        summary = _load_summary(summary_path)
        artifacts = summary.get("artifacts") or {}
        eq_rel = artifacts.get("equity")
        if not eq_rel:
            raise RuntimeError(f"Equity artifact missing for {label}")
        series = _load_equity_series(eq_rel)
        equity_series[label] = series
        runs.append(
            {
                "label": label,
                "symbol": summary.get("symbol"),
                "config_name": meta["config_name"],
                "config_path": str(cfg),
                "run_id": summary.get("run_id"),
                "summary_path": str(summary_path),
                "metrics": summary.get("metrics", {}),
            }
        )

    equity_df, normalized_weights = _combine_equity(equity_series, weight_map)
    sleeve_labels = list(equity_series.keys())
    returns_df = equity_df[sleeve_labels].pct_change().fillna(0.0)
    corr_df = returns_df.corr()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / f"{tag}_{timestamp}"
    summary_path = _write_outputs(out_dir, tag, runs, equity_df, returns_df, corr_df, normalized_weights)
    logger.info("Portfolio summary written to %s", summary_path)
    return summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multiple configs and aggregate into a portfolio.")
    parser.add_argument(
        "--configs",
        nargs="+",
        required=True,
        help="List of YAML config paths (each run independently and aggregated).",
    )
    parser.add_argument("--tag", type=str, default="portfolio_fx", help="Tag for output folder naming.")
    parser.add_argument(
        "--results-root",
        type=str,
        default=str(Path(BASE_DIR) / "results"),
        help="Directory to stash portfolio summary outputs.",
    )
    parser.add_argument("--weight-file", type=str, help="Optional JSON file containing sleeve weights.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg_paths = [Path(p).expanduser().resolve() for p in args.configs]
    out_root = Path(args.results_root).expanduser().resolve()
    weight_map = None
    if args.weight_file:
        weight_map = _load_weights(Path(args.weight_file).expanduser().resolve())
    run_portfolio(cfg_paths, args.tag, out_root, weight_map)


if __name__ == "__main__":
    main()
