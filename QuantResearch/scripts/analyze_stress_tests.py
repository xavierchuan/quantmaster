#!/usr/bin/env python3
"""
Aggregate FX stress-test runs and emit a consolidated Markdown report.

Expected inputs (relative to repo root):
  - QuantResearch/results/stress_tests/baseline_runs.json
       {
         "GBPUSD": "QuantResearch/results/<run_id>/summary.json",
         ...
       }
  - QuantResearch/results/stress_tests/sliding_window_runs.json
  - QuantResearch/results/stress_tests/cost_pressure_runs.json
  - QuantResearch/results/stress_tests/vol_warp_runs.json

Outputs:
  - Markdown report QuantResearch/docs/stress_tests.md
  - Figures under QuantResearch/docs/stress_tests/*.png
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib.pyplot as plt


Metric = Dict[str, Optional[float]]


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_summary(summary_path: Path) -> dict:
    data = read_json(summary_path)
    metrics = data.get("metrics", {})
    return {
        "run_id": data.get("run_id"),
        "symbol": data.get("symbol"),
        "summary_path": str(summary_path),
        "ann_return": metrics.get("ann_return"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown": metrics.get("max_drawdown"),
        "trades": metrics.get("trades"),
    }


def delta(new: Optional[float], base: Optional[float]) -> Optional[float]:
    if new is None or base is None:
        return None
    return new - base


def delta_trades(new: Optional[float], base: Optional[float]) -> Optional[float]:
    if new is None or base is None:
        return None
    return new - base


def format_float(value: Optional[float], pct: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%" if pct else f"{value:.3f}"


def format_int(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(value))}"


def format_int_delta(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.0f}"


def determine_pass_fail(
    sharpe_values: List[Optional[float]],
    trade_values: List[Optional[float]],
    threshold: float,
    require_trades: bool = True,
) -> str:
    sharpe_pass = sum(1 for s in sharpe_values if s is not None and s >= threshold)
    needed = math.ceil(len(sharpe_values) / 2.0)
    trades_ok = True
    if require_trades:
        trades_ok = all(t is not None and t > 0 for t in trade_values)
    return "PASS" if sharpe_pass >= needed and trades_ok else "FAIL"


def generate_sliding_plot(symbol: str, windows: dict, baseline: dict, output_dir: Path) -> str:
    order = sorted(windows.keys())
    sharpe = [windows[w]["sharpe"] for w in order]
    plt.figure(figsize=(5, 3))
    plt.plot(order, sharpe, marker="o", label="Sliding Sharpe")
    if baseline["sharpe"] is not None:
        plt.axhline(baseline["sharpe"], color="gray", linestyle="--", label="Baseline Sharpe")
    plt.title(f"{symbol} Sliding Sharpe")
    plt.ylabel("Sharpe")
    plt.tight_layout()
    out_path = output_dir / f"sliding_{symbol.lower()}.png"
    plt.legend()
    plt.savefig(out_path)
    plt.close()
    return str(out_path.relative_to(output_dir.parent))


def generate_single_bar_plot(symbol: str, title: str, stress_name: str, stress_value: Optional[float], baseline_value: Optional[float], output_dir: Path, suffix: str) -> str:
    plt.figure(figsize=(4, 3))
    labels = ["Baseline", stress_name]
    values = [baseline_value or 0.0, stress_value or 0.0]
    colors = ["#4c72b0", "#dd8452"]
    plt.bar(labels, values, color=colors)
    plt.ylabel("Sharpe")
    plt.title(f"{symbol} {title}")
    plt.tight_layout()
    out_path = output_dir / f"{suffix}_{symbol.lower()}.png"
    plt.savefig(out_path)
    plt.close()
    return str(out_path.relative_to(output_dir.parent))


def build_report(
    baselines: Dict[str, dict],
    sliding: Dict[str, dict],
    cost_runs: Dict[str, List[dict]],
    vol_runs: Dict[str, List[dict]],
    vol_spike_runs: Dict[str, List[dict]],
    generic_runs: Dict[str, Dict[str, List[dict]]],
    out_md: Path,
    figs_dir: Path,
    pass_sharpe_threshold: float,
) -> None:
    figs_dir.mkdir(parents=True, exist_ok=True)
    lines: List[str] = ["# Overfitting Stress Tests", ""]

    # Baseline table
    lines.append("## Baseline Metrics")
    lines.append("| Symbol | Run ID | Sharpe | AnnRet | MaxDD | Trades |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for symbol, metrics in baselines.items():
        lines.append(
            f"| {symbol} | {metrics.get('run_id','?')} | {format_float(metrics['sharpe'])} | "
            f"{format_float(metrics['ann_return'], pct=True)} | {format_float(metrics['max_drawdown'], pct=True)} | {format_int(metrics.get('trades'))} |"
        )
    lines.append("")

    # Sliding windows
    lines.append("## Sliding Window Tests")
    for symbol, windows in sliding.items():
        if symbol not in baselines:
            continue
        baseline = baselines[symbol]
        fig_rel = generate_sliding_plot(symbol, windows, baseline, figs_dir)
        pass_fail = determine_pass_fail([windows[w]["sharpe"] for w in windows], [windows[w]["trades"] for w in windows], pass_sharpe_threshold)
        lines.append(f"### {symbol} — {pass_fail}")
        lines.append(f"![Sliding {symbol}]({fig_rel})")
        lines.append("| Window | Run ID | Sharpe | ΔSharpe | AnnRet | ΔAnnRet | MaxDD | ΔMaxDD | Trades | ΔTrades |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for window in sorted(windows.keys()):
            entry = windows[window]
            lines.append(
                f"| {window} | {entry.get('backtest_run_id','?')} | {format_float(entry['sharpe'])} | "
                f"{format_float(delta(entry['sharpe'], baseline['sharpe']))} | "
                f"{format_float(entry['ann_return'], pct=True)} | "
                f"{format_float(delta(entry['ann_return'], baseline['ann_return']), pct=True)} | "
                f"{format_float(entry['max_drawdown'], pct=True)} | "
                f"{format_float(delta(entry['max_drawdown'], baseline['max_drawdown']), pct=True)} | "
                f"{format_int(entry.get('trades'))} | "
                f"{format_int_delta(delta_trades(entry.get('trades'), baseline['trades']))} |"
            )
        lines.append("")

    # Cost pressure
    lines.append("## Cost Pressure Tests (spread/slip/comm × 1.5)")
    lines.append("| Symbol | Status | Run ID | Sharpe | ΔSharpe | AnnRet | ΔAnnRet | MaxDD | ΔMaxDD | Trades | ΔTrades | Chart |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for symbol, runs in cost_runs.items():
        if symbol not in baselines:
            continue
        baseline = baselines[symbol]
        entry = runs[-1]
        status = "PASS" if entry.get("sharpe") and entry.get("sharpe") >= pass_sharpe_threshold and entry.get("trades", 0) > 0 else "FAIL"
        fig_rel = generate_single_bar_plot(symbol, "Cost Sharpe", "Cost", entry.get("sharpe"), baseline["sharpe"], figs_dir, "cost")
        lines.append(
            f"| {symbol} | {status} | {entry.get('run_id','?')} | {format_float(entry.get('sharpe'))} | "
            f"{format_float(delta(entry.get('sharpe'), baseline['sharpe']))} | "
            f"{format_float(entry.get('ann_return'), pct=True)} | "
            f"{format_float(delta(entry.get('ann_return'), baseline['ann_return']), pct=True)} | "
            f"{format_float(entry.get('max_drawdown'), pct=True)} | "
            f"{format_float(delta(entry.get('max_drawdown'), baseline['max_drawdown']), pct=True)} | "
            f"{format_int(entry.get('trades'))} | "
            f"{format_int_delta(delta_trades(entry.get('trades'), baseline['trades']))} | "
            f"![Cost {symbol}]({fig_rel}) |"
        )
    lines.append("")

    # Vol warp
    lines.append("## Volatility Warp Tests (σ=0.05)")
    lines.append("| Symbol | Status | Run ID | Sharpe | ΔSharpe | Trades | Notes | Chart |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for symbol, runs in vol_runs.items():
        if symbol not in baselines:
            continue
        baseline = baselines[symbol]
        entry = runs[-1]
        trades = entry.get("trades")
        status = "PASS" if trades and trades > 0 else "FAIL"
        note = "No trades under warp" if not trades else ""
        fig_rel = generate_single_bar_plot(symbol, "Vol Warp Sharpe", "Warp", entry.get("sharpe"), baseline["sharpe"], figs_dir, "volwarp")
        lines.append(
            f"| {symbol} | {status} | {entry.get('run_id','?')} | {format_float(entry.get('sharpe'))} | "
            f"{format_float(delta(entry.get('sharpe'), baseline['sharpe']))} | "
            f"{format_int(trades)} | {note} | "
            f"![Vol {symbol}]({fig_rel}) |"
        )

    # Vol spike (backtest-side)
    if vol_spike_runs:
        lines.append("")
        lines.append("## Volatility Spike Tests")
        lines.append("| Symbol | Status | Run ID | Sharpe | ΔSharpe | Trades | Notes | Chart |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for symbol, runs in vol_spike_runs.items():
            if symbol not in baselines:
                continue
            baseline = baselines[symbol]
            entry = runs[-1]
            metrics = entry.get("metrics", {})
            trades = metrics.get("trades")
            status = "PASS" if trades and trades > 0 else "FAIL"
            note = ""
            fig_rel = generate_single_bar_plot(symbol, "Vol Spike Sharpe", "Spike", metrics.get("sharpe"), baseline["sharpe"], figs_dir, "volspike")
            lines.append(
                f"| {symbol} | {status} | {entry.get('run_id','?')} | {format_float(metrics.get('sharpe'))} | "
                f"{format_float(delta(metrics.get('sharpe'), baseline['sharpe']))} | "
                f"{format_int(trades)} | {note} | "
                f"![VolSpike {symbol}]({fig_rel}) |"
            )

    # Generic stress groups (label shuffle/noise, block shuffle, regime dropout, feature ablation/dropout, drop sample, vol warp window)
    for section_title, runs_dict in generic_runs.items():
        if not runs_dict:
            continue
        lines.append("")
        lines.append(f"## {section_title}")
        lines.append("| Symbol | Status | Run ID | Sharpe | ΔSharpe | AnnRet | ΔAnnRet | MaxDD | ΔMaxDD | Trades | ΔTrades | Chart |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for symbol, runs in runs_dict.items():
            if symbol not in baselines or not runs:
                continue
            baseline = baselines[symbol]
            entry = runs[-1]
            metrics = entry.get("metrics", entry)
            sharpe = metrics.get("sharpe")
            trades = metrics.get("trades")
            status = "PASS" if trades and trades > 0 and (sharpe is None or sharpe >= pass_sharpe_threshold) else "FAIL"
            fig_rel = generate_single_bar_plot(symbol, section_title, section_title, sharpe, baseline["sharpe"], figs_dir, section_title.replace(" ", "_").lower())
            lines.append(
                f"| {symbol} | {status} | {entry.get('run_id','?')} | {format_float(sharpe)} | "
                f"{format_float(delta(sharpe, baseline['sharpe']))} | "
                f"{format_float(metrics.get('ann_return'), pct=True)} | "
                f"{format_float(delta(metrics.get('ann_return'), baseline['ann_return']), pct=True)} | "
                f"{format_float(metrics.get('max_drawdown'), pct=True)} | "
                f"{format_float(delta(metrics.get('max_drawdown'), baseline['max_drawdown']), pct=True)} | "
                f"{format_int(trades)} | "
                f"{format_int_delta(delta_trades(trades, baseline['trades']))} | "
                f"![{section_title} {symbol}]({fig_rel}) |"
            )

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate stress-test report.")
    parser.add_argument(
        "--baseline-map",
        default="QuantResearch/results/stress_tests/baseline_runs.json",
        help="JSON mapping symbol -> baseline summary path",
    )
    parser.add_argument(
        "--sliding",
        default="QuantResearch/results/stress_tests/sliding_window_runs.json",
        help="Sliding-window results JSON",
    )
    parser.add_argument(
        "--cost",
        default="QuantResearch/results/stress_tests/cost_pressure_runs.json",
        help="Cost pressure results JSON",
    )
    parser.add_argument(
        "--vol-warp",
        dest="vol",
        default="QuantResearch/results/stress_tests/vol_warp_runs.json",
        help="Volatility warp results JSON",
    )
    parser.add_argument(
        "--vol-spike",
        dest="vol_spike",
        default="QuantResearch/results/stress_tests/vol_spike_2020q2_runs.json",
        help="Volatility spike results JSON",
    )
    parser.add_argument("--label-shuffle", default="QuantResearch/results/stress_tests/label_shuffle_runs.json", help="Label shuffle results JSON")
    parser.add_argument("--label-noise", default="QuantResearch/results/stress_tests/label_noise_0p5_runs.json", help="Label noise results JSON")
    parser.add_argument("--block-shuffle", default="QuantResearch/results/stress_tests/block_shuffle_250_runs.json", help="Block shuffle results JSON")
    parser.add_argument("--regime-dropout", dest="regime_dropout", default="QuantResearch/results/stress_tests/regime_dropout_0p4_runs.json", help="Regime dropout results JSON")
    parser.add_argument("--feature-ablation-vol24", default="QuantResearch/results/stress_tests/feature_ablation_vol24_runs.json", help="Feature ablation vol_24 results JSON")
    parser.add_argument("--feature-ablation-sma-diff", default="QuantResearch/results/stress_tests/feature_ablation_sma_diff_runs.json", help="Feature ablation sma_diff results JSON")
    parser.add_argument("--feature-ablation-vol24-sma", default="QuantResearch/results/stress_tests/feature_ablation_vol24_sma_runs.json", help="Feature ablation vol_24+sma_diff results JSON")
    parser.add_argument("--feature-dropout", default="QuantResearch/results/stress_tests/feature_dropout_0p1_runs.json", help="Feature dropout results JSON")
    parser.add_argument("--drop-sample", default="QuantResearch/results/stress_tests/drop_sample_0p3_runs.json", help="Drop sample results JSON")
    parser.add_argument("--vol-warp-window", default="QuantResearch/results/stress_tests/vol_warp_window_2020q2_runs.json", help="Vol warp window results JSON")
    parser.add_argument(
        "--output-md",
        default="QuantResearch/docs/stress_tests.md",
        help="Markdown output path",
    )
    parser.add_argument(
        "--fig-dir",
        default="QuantResearch/docs/stress_tests",
        help="Directory for generated figures",
    )
    parser.add_argument(
        "--pass-sharpe-threshold",
        type=float,
        default=0.0,
        help="Sharpe threshold for PASS evaluation",
    )
    args = parser.parse_args()

    baseline_map = read_json(Path(args.baseline_map))
    baselines = {symbol: load_summary(Path(path)) for symbol, path in baseline_map.items()}
    sliding = read_json(Path(args.sliding))
    cost_runs = read_json(Path(args.cost))
    vol_runs = read_json(Path(args.vol))
    vol_spike_runs = read_json(Path(args.vol_spike)) if Path(args.vol_spike).exists() else {}

    generic_runs = {
        "Label Shuffle": read_json(Path(args.label_shuffle)) if Path(args.label_shuffle).exists() else {},
        "Label Noise": read_json(Path(args.label_noise)) if Path(args.label_noise).exists() else {},
        "Block Shuffle": read_json(Path(args.block_shuffle)) if Path(args.block_shuffle).exists() else {},
        "Regime Dropout": read_json(Path(args.regime_dropout)) if Path(args.regime_dropout).exists() else {},
        "Feature Ablation vol_24": read_json(Path(args.feature_ablation_vol24)) if Path(args.feature_ablation_vol24).exists() else {},
        "Feature Ablation sma_diff": read_json(Path(args.feature_ablation_sma_diff)) if Path(args.feature_ablation_sma_diff).exists() else {},
        "Feature Ablation vol_24+sma_diff": read_json(Path(args.feature_ablation_vol24_sma)) if Path(args.feature_ablation_vol24_sma).exists() else {},
        "Feature Dropout": read_json(Path(args.feature_dropout)) if Path(args.feature_dropout).exists() else {},
        "Drop Sample": read_json(Path(args.drop_sample)) if Path(args.drop_sample).exists() else {},
        "Vol Warp Window": read_json(Path(args.vol_warp_window)) if Path(args.vol_warp_window).exists() else {},
    }

    build_report(
        baselines=baselines,
        sliding=sliding,
        cost_runs=cost_runs,
        vol_runs=vol_runs,
        vol_spike_runs=vol_spike_runs,
        generic_runs=generic_runs,
        out_md=Path(args.output_md),
        figs_dir=Path(args.fig_dir),
        pass_sharpe_threshold=args.pass_sharpe_threshold,
    )


if __name__ == "__main__":
    main()
