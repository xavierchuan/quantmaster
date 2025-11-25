#!/usr/bin/env python3
"""
Automate FX overfitting stress tests.

For each symbol/test combination:
  1. Train a stress model with train_xgb_usdjpy.py using provided train_params.
  2. Temporarily point the symbol's latest.json to the stress pointer.
  3. Run backtest_strategy.py with baseline config + optional backtest_params.
  4. Move the run directory to results/stress_tests/<test>/<symbol>/<run_id>/.
  5. Append metadata to results/stress_tests/<test>_runs.json.

Usage:
    python QuantResearch/scripts/run_stress_tests.py --symbols GBPUSD,EURUSD
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_ROOT = REPO_ROOT / "QuantResearch" / "results"
STRESS_RESULTS_ROOT = RESULTS_ROOT / "stress_tests"
ARTIFACT_ROOT = REPO_ROOT / "QuantResearch" / "artifacts" / "stress"

SYMBOL_CONFIG: Dict[str, str] = {
    "GBPUSD": "QuantResearch/config/gbpusd_h1_xgb_baseline.yaml",
    "EURUSD": "QuantResearch/config/eurusd_h1_xgb_baseline.yaml",
    "USDCHF": "QuantResearch/config/usdchf_h1_xgb_baseline.yaml",
    "AUDUSD": "QuantResearch/config/audusd_h1_xgb_baseline.yaml",
    "GBPJPY": "QuantResearch/config/gbpjpy_h1_xgb_baseline.yaml",
    "USDJPY": "QuantResearch/config/usdjpy_xgb_backtest.yaml",
}

MODEL_POINTERS: Dict[str, Path] = {
    symbol: REPO_ROOT / f"QuantResearch/artifacts/models/{symbol.lower()}_h1_xgb_latest.json"
    for symbol in SYMBOL_CONFIG
}

# Each entry can specify training params and/or backtest params
STRESS_TEST_MATRIX: List[Dict[str, object]] = [
    # Label/temporal randomization
    {"name": "label_shuffle", "train_params": ["--label-shuffle", "1.0"], "backtest_params": [], "description": "Random label permutation"},
    {"name": "label_noise_0p5", "train_params": ["--label-noise", "0.5"], "backtest_params": [], "description": "Random label flip p=0.5"},
    {"name": "block_shuffle_250", "train_params": ["--block-shuffle", "250"], "backtest_params": [], "description": "Shuffle samples in 250-row blocks"},
    # Regime robustness
    {"name": "regime_dropout_0p4", "train_params": ["--drop-regime", "0.4"], "backtest_params": [], "description": "Drop 40% of regime rows"},
    # Feature ablation
    {"name": "feature_ablation_vol24", "train_params": ["--feature-mask", "vol_24"], "backtest_params": [], "description": "Remove vol_24 feature"},
    {"name": "feature_ablation_sma_diff", "train_params": ["--feature-mask", "sma_diff"], "backtest_params": [], "description": "Remove sma_diff"},
    {"name": "feature_ablation_vol24_sma", "train_params": ["--feature-mask", "vol_24,sma_diff"], "backtest_params": [], "description": "Remove vol_24 + sma_diff"},
    {"name": "feature_dropout_0p1", "train_params": ["--feature-drop-rate", "0.1"], "backtest_params": [], "description": "Stochastic feature dropout p=0.1"},
    # Sample dilution
    {"name": "drop_sample_0p3", "train_params": ["--drop-sample", "0.3"], "backtest_params": [], "description": "Randomly drop 30% samples"},
    # Volatility warp (train)
    {"name": "vol_warp_0p05", "train_params": ["--vol-warp", "0.05"], "backtest_params": [], "description": "Global vol warp sigma=0.05"},
    {"name": "vol_warp_window_2020q2", "train_params": ["--vol-warp-window", "2020-03-01", "2020-05-30", "--vol-warp", "0.10"], "backtest_params": [], "description": "Vol warp in 2020-03~05"},
    # Cost & vol spike (backtest side only)
    {"name": "cost_stress_1p5", "train_params": [], "backtest_params": ["--stress-cost-spread-mult", "1.5", "--stress-cost-comm-mult", "1.5", "--stress-slippage-mult", "1.5"], "description": "Cost/slip x1.5"},
    {"name": "vol_spike_2020q2", "train_params": [], "backtest_params": ["--stress-vol-spike-window", "2020-03-01", "2020-05-30", "--stress-vol-mult", "1.2"], "description": "Vol spike on high/low in 2020-03~05"},
]


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return proc.stdout


def capture_new_run_id(before: set[str], after: set[str]) -> str:
    new_runs = sorted([r for r in after - before if r[:8].isdigit()])
    if not new_runs:
        raise RuntimeError("Unable to determine new run_id (no new directories found).")
    return new_runs[-1]


def list_run_dirs() -> set[str]:
    return {p.name for p in RESULTS_ROOT.iterdir() if p.is_dir() and p.name[:8].isdigit()}


def ensure_pointer(symbol: str) -> Path:
    pointer = MODEL_POINTERS.get(symbol)
    if not pointer or not pointer.exists():
        raise FileNotFoundError(f"Pointer file missing for {symbol}: {pointer}")
    return pointer


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def update_log(test_name: str, symbol: str, entry: dict) -> None:
    log_path = STRESS_RESULTS_ROOT / f"{test_name}_runs.json"
    log = load_json(log_path) if log_path.exists() else {}
    log.setdefault(symbol, []).append(entry)
    write_json(log_path, log)


def run_single_test(symbol: str, test_cfg: dict, dry_run: bool = False) -> None:
    test_name = test_cfg["name"]
    train_params: List[str] = list(test_cfg.get("train_params", []))
    backtest_params: List[str] = list(test_cfg.get("backtest_params", []))
    description = test_cfg.get("description", "")
    symbol_lower = symbol.lower()
    artifact_dir = ARTIFACT_ROOT / test_name / symbol_lower
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stress_pointer = artifact_dir / "latest.json"

    train_cmd = [
        "python",
        "QuantResearch/scripts/train_xgb_usdjpy.py",
        "--symbol",
        symbol,
        "--out",
        str(artifact_dir),
        "--latest-ptr",
        str(stress_pointer),
    ] + train_params
    config_path = SYMBOL_CONFIG[symbol]
    backtest_cmd = [
        "python",
        "QuantResearch/scripts/backtest_strategy.py",
        "--config",
        config_path,
    ] + backtest_params

    print(f"\n=== {symbol} / {test_name} ===")
    print("Train:", " ".join(train_cmd))
    print("Backtest:", " ".join(backtest_cmd))
    if description:
        print("Description:", description)
    if dry_run:
        return

    model_dir = None
    train_stdout = run_cmd(train_cmd, cwd=REPO_ROOT)
    for line in train_stdout.strip().splitlines()[::-1]:
        if "Saved model artifacts to" in line:
            model_dir = line.split("Saved model artifacts to", 1)[1].strip()
            break
    if not model_dir:
        raise RuntimeError("Failed to parse model directory from training output.")
    if not stress_pointer.exists():
        raise RuntimeError(f"Stress pointer not written: {stress_pointer}")

    stress_pointer_text = stress_pointer.read_text(encoding="utf-8")
    pointer_path = ensure_pointer(symbol)
    original_pointer_text = pointer_path.read_text(encoding="utf-8")
    before_dirs = list_run_dirs()
    try:
        pointer_path.write_text(stress_pointer_text, encoding="utf-8")
        run_cmd(backtest_cmd, cwd=REPO_ROOT)
    finally:
        pointer_path.write_text(original_pointer_text, encoding="utf-8")
    after_dirs = list_run_dirs()
    new_run_id = capture_new_run_id(before_dirs, after_dirs)
    run_src = RESULTS_ROOT / new_run_id
    dest_dir = STRESS_RESULTS_ROOT / test_name / symbol / new_run_id
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_src), dest_dir)
    summary_path = dest_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary not found: {summary_path}")
    summary = load_json(summary_path)
    metrics = summary.get("metrics", {})
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "test": test_name,
        "run_id": new_run_id,
        "summary_path": str(summary_path.relative_to(REPO_ROOT)),
        "train_model_dir": model_dir,
        "train_params": train_params,
        "backtest_params": backtest_params,
        "description": description,
        "metrics": {
            "sharpe": metrics.get("sharpe"),
            "ann_return": metrics.get("ann_return"),
            "max_drawdown": metrics.get("max_drawdown"),
            "trades": metrics.get("trades"),
        },
    }
    update_log(test_name, symbol, entry)
    print(f"Completed {symbol} / {test_name}: run_id={new_run_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch FX stress tests.")
    parser.add_argument(
        "--symbols",
        default=",".join(SYMBOL_CONFIG.keys()),
        help="Comma-separated symbols to run (default: all)",
    )
    parser.add_argument(
        "--tests",
        default=",".join([cfg["name"] for cfg in STRESS_TEST_MATRIX]),
        help="Comma-separated stress test names (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    args = parser.parse_args()

    selected_symbols = [sym.strip().upper() for sym in args.symbols.split(",") if sym.strip()]
    selected_tests = [name.strip() for name in args.tests.split(",") if name.strip()]
    missing_symbols = [sym for sym in selected_symbols if sym not in SYMBOL_CONFIG]
    if missing_symbols:
        raise ValueError(f"Unknown symbols: {missing_symbols}")
    test_map = {cfg["name"]: cfg for cfg in STRESS_TEST_MATRIX}
    missing_tests = [test for test in selected_tests if test not in test_map]
    if missing_tests:
        raise ValueError(f"Unknown test names: {missing_tests}")

    for symbol in selected_symbols:
        for test_name in selected_tests:
            run_single_test(symbol, test_map[test_name], dry_run=args.dry_run)


if __name__ == "__main__":
    main()
