#!/usr/bin/env python3
"""
Portfolio-level paper trading launcher.

Features:
- Read weight file (JSON) mapping config filename -> weight
- Accept a list of strategy configs
- Allocate per-sleeve qty based on total cash and weight; respects base cash/qty in YAML
- Optional caps on qty scaling
- Sequentially launches paper_trade.py for each sleeve
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, List
import sys

TRADER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TRADER_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from shared.utils.oanda_client import snapshot_account

import yaml


def load_weights(path: Path) -> Dict[str, float]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: float(v) for k, v in data.items()}


def load_cfg(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def compute_qty(cfg: Dict, weight: float, total_cash: float, max_qty_mult: float, min_qty: float) -> float:
    base_cash = float(cfg.get("cash", 100000.0))
    base_qty = float(cfg.get("qty", 10000.0))
    if base_cash <= 0:
        scale = weight
    else:
        scale = (total_cash * weight) / base_cash
    qty = base_qty * scale
    qty = min(qty, base_qty * max_qty_mult)
    qty = max(qty, min_qty)
    return qty


def main() -> None:
    ap = argparse.ArgumentParser(description="Launch multiple paper_trade jobs with portfolio weights.")
    ap.add_argument("--configs", nargs="+", required=True, help="List of strategy YAML configs.")
    ap.add_argument("--weight-file", required=True, help="JSON mapping config filename -> weight.")
    ap.add_argument("--total-cash", type=float, default=None, help="Total capital in base currency (default: fetch NAV from OANDA).")
    ap.add_argument("--environment", default="practice", choices=["practice", "live"], help="OANDA environment.")
    ap.add_argument("--timeframe", default="60s", help="Bar timeframe (default 60s).")
    ap.add_argument("--max-qty-mult", type=float, default=3.0, help="Cap qty to base_qty * max_qty_mult.")
    ap.add_argument("--min-qty", type=float, default=0.0, help="Minimum qty after scaling.")
    ap.add_argument("--dry-run", action="store_true", help="Print commands only, do not launch.")
    ap.add_argument(
        "--log-heartbeat",
        action="store_true",
        help="Enable OANDA PricingStream heartbeat logs in child paper_trade processes.",
    )
    args = ap.parse_args()

    weights = load_weights(Path(args.weight_file))
    cmds: List[List[str]] = []

    def resolve_total_cash() -> float:
        if args.total_cash is not None:
            return args.total_cash
        try:
            snapshot = snapshot_account()
        except Exception as exc:
            raise RuntimeError(
                "Failed to fetch account NAV. Provide --total-cash explicitly."
            ) from exc
        nav = snapshot.get("nav")
        if nav in (None, 0, ""):
            raise RuntimeError(
                "snapshot_account returned empty NAV. Provide --total-cash explicitly."
            )
        print(f"[NAV] Using account NAV={float(nav):.2f} from snapshot")
        return float(nav)

    total_cash = resolve_total_cash()

    for cfg_path_str in args.configs:
        cfg_path = Path(cfg_path_str).resolve()
        cfg = load_cfg(cfg_path)
        cfg_name = cfg_path.name
        if cfg_name not in weights:
            raise RuntimeError(f"{cfg_name} missing in weight file {args.weight_file}")
        w = weights[cfg_name]
        qty = compute_qty(cfg, w, total_cash, args.max_qty_mult, args.min_qty)
        if qty <= 0:
            print(f"[SKIP] {cfg_name} weight={w:.4f} qty={qty:.2f}")
            continue
        cmd = [
            "python",
            str(Path(__file__).resolve().parent / "paper_trade.py"),
            "--config",
            str(cfg_path),
            "--timeframe",
            args.timeframe,
            "--environment",
            args.environment,
            *( ["--log-heartbeat"] if args.log_heartbeat else [] ),
            "--qty",
            f"{qty:.0f}",
        ]
        cmds.append(cmd)
        print(f"[PLAN] {cfg_name} weight={w:.4f} qty={qty:.0f} cmd={' '.join(cmd)}")

    if args.dry_run:
        return

    for cmd in cmds:
        subprocess.Popen(cmd)


if __name__ == "__main__":
    main()
