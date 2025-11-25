#!/usr/bin/env python3
"""
Run baseline backtests across multiple FX pairs using xgb_signal
with per-pair data/models already prepared.

Assumes:
- Clean data with regime labels: data/clean/{PAIR}_H1_clean_v2_with_regime.csv
- Latest model pointers:
    artifacts/models/{pair_lower}_h1_xgb_latest.json
    artifacts/models/{pair_lower}_trend_regime_latest.json

Outputs a summary CSV at results/batch_fx_baseline.csv
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

import pandas as pd

CURRENT = Path(__file__).resolve()
PROJECT_ROOT = CURRENT.parents[2]
RESEARCH_ROOT = PROJECT_ROOT / "QuantResearch"
for p in (PROJECT_ROOT, RESEARCH_ROOT):
    if str(p) not in sys.path:
        sys.path.append(str(p))

from scripts.backtest_strategy import run_once  # type: ignore
from QuantResearch.core.backtest.strategy_engine import parse_strategy_specs  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "QuantResearch" / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "QuantResearch" / "artifacts" / "models"
RESULTS_DIR = PROJECT_ROOT / "QuantResearch" / "results"


PAIRS: List[Dict] = [
    {"symbol": "GBPUSD", "spread": 1.6, "slip": 0.2},  # strong
    {"symbol": "EURUSD", "spread": 1.2, "slip": 0.2},  # strong
    {"symbol": "USDCHF", "spread": 1.5, "slip": 0.2},  # strong
    {"symbol": "AUDUSD", "spread": 1.5, "slip": 0.2},  # mid
    {"symbol": "GBPJPY", "spread": 2.0, "slip": 0.2},  # mid / high risk
]


def pointer_path(symbol_lower: str, kind: str) -> Path:
    return ARTIFACTS_DIR / f"{symbol_lower}_{kind}_latest.json"


def latest_model_dir(ptr_file: Path) -> str:
    payload = json.loads(ptr_file.read_text())
    model_dir = payload.get("model_dir")
    if not model_dir:
        raise RuntimeError(f"{ptr_file} missing model_dir")
    return model_dir


def build_strategy_spec(symbol: str) -> List[Dict]:
    sym_lower = symbol.lower()
    latest_ptr = pointer_path(sym_lower, "h1_xgb")
    trend_ptr = pointer_path(sym_lower, "trend_regime")
    if not latest_ptr.exists() or not trend_ptr.exists():
        raise FileNotFoundError(f"Missing model pointer for {symbol}")
    return [
        {
            "name": "xgb_signal",
            "weight": 1.0,
            "params": {
                "latest_ptr": str(latest_ptr),
                "trend_ptr": str(trend_ptr),
                "prob_long": 0.60,
                "prob_exit": 0.52,
                "size_mult": 1.0,
                "cooldown_bars": 5,
                "regime_filter": {"trend": ["trend_up", "trend_down"]},
                "session_block_hours": [[0, 6]],
                "enable_short_signals": True,
                "prob_short": 0.58,
                "prob_short_exit": 0.48,
                "short_size_mult": 1.0,
                "vol_high_prob_delta": -0.10,
                "vol_low_prob_delta": 0.03,
                "vol_high_size_mult": 0.7,
                "vol_low_size_mult": 1.0,
                "vol_high_cooldown": 10,
                "vol_low_cooldown": 5,
                "vol_high_atr_sl_mult": 0.60,
                "vol_high_atr_tp_mult": 0.40,
                "vol_low_atr_sl_mult": 1.0,
                "vol_low_atr_tp_mult": 1.0,
                "trend_up_prob_delta": -0.02,
                "trend_down_prob_delta": 0.03,
                "trend_chop_prob_delta": 0.01,
                "trend_up_size_mult": 1.1,
                "trend_down_size_mult": 0.9,
                "trend_chop_size_mult": 0.9,
                "trend_up_cooldown": 4,
                "trend_down_cooldown": 8,
                "trend_chop_cooldown": 7,
                "trend_up_short_prob_delta": 0.02,
                "trend_down_short_prob_delta": -0.02,
                "trend_chop_short_prob_delta": 0.0,
                "trend_up_short_size_mult": 0.9,
                "trend_down_short_size_mult": 1.1,
                "trend_chop_short_size_mult": 0.9,
                "trend_up_short_cooldown": 6,
                "trend_down_short_cooldown": 6,
                "trend_chop_short_cooldown": 7,
                "trend_up_atr_sl_mult": 1.05,
                "trend_up_atr_tp_mult": 1.18,
                "trend_down_atr_sl_mult": 0.74,
                "trend_down_atr_tp_mult": 0.58,
                "trend_chop_atr_sl_mult": 1.0,
                "trend_chop_atr_tp_mult": 1.0,
            },
        }
    ]


def main() -> None:
    records = []
    for entry in PAIRS:
        symbol = entry["symbol"]
        sym_lower = symbol.lower()
        csv_path = DATA_DIR / "clean" / f"{symbol}_H1_clean_v2_with_regime.csv"
        if not csv_path.exists():
            print(f"[skip] {symbol} missing clean data: {csv_path}")
            continue
        try:
            strat_specs = parse_strategy_specs(build_strategy_spec(symbol))
        except Exception as exc:
            print(f"[skip] {symbol} spec error: {exc}")
            continue
        res = run_once(
            symbol=symbol,
            csv_path=str(csv_path),
            initial_cash=1_000_000.0,
            qty=1_000_000,
            account_ccy="USD",
            fast_win=20,
            slow_win=80,
            spread_pips=float(entry["spread"]),
            commission_per_million=0.25,
            slippage_pips=float(entry["slip"]),
            atr_sl=1.9,
            atr_tp=3.8,
            atr_window=14,
            regime_ema_window=200,
            regime_trend_min_bars=0,
            cooldown=0,
            long_only_above_slow=True,
            allow_short=True,
            short_only_below_slow=True,
            risk_per_trade_pct=0.05,
            max_drawdown_pct=0.10,
            max_position_units=5_000_000,
            strategies=strat_specs,
            strategy_mode="first_hit",
            validate_data=False,
        )
        records.append({
            "symbol": symbol,
            "ann_return": res.get("ann_return"),
            "sharpe": res.get("sharpe"),
            "max_drawdown": res.get("max_drawdown"),
            "trades": res.get("trades"),
            "run_id": res.get("run_id"),
            "summary_path": res.get("summary_path"),
        })
    if records:
        df = pd.DataFrame(records)
        out = RESULTS_DIR / "batch_fx_baseline.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"[batch] saved overview to {out}")
        print(df.to_string(index=False))
    else:
        print("No records produced.")


if __name__ == "__main__":
    main()
