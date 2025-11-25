#!/usr/bin/env python3
"""Build live equity and daily PnL reports from fills."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Export live equity + daily PnL from fills")
    ap.add_argument("--fills", default="QuantTrader/results/execution/live/fills.csv")
    ap.add_argument("--snapshots", default="QuantTrader/results/execution/account_snapshots.csv")
    ap.add_argument("--out-equity", default="QuantTrader/results/execution/live_equity.csv")
    ap.add_argument("--out-daily", default="QuantTrader/results/execution/daily_pnl.csv")
    ap.add_argument("--initial-cash", type=float, default=None, help="Override starting equity")
    return ap.parse_args()


def infer_initial(snapshot_path: Path) -> float | None:
    if not snapshot_path.exists():
        return None
    df = pd.read_csv(snapshot_path)
    if df.empty:
        return None
    return float(df.iloc[0].get("balance", 0.0))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    fills_path = Path(args.fills)
    if not fills_path.exists():
        raise SystemExit(f"fills CSV not found: {fills_path}")
    df = pd.read_csv(fills_path)
    if df.empty:
        raise SystemExit("fills CSV is empty")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    if "pnl" not in df.columns:
        raise SystemExit("fills CSV missing pnl column")

    start_equity = args.initial_cash
    if start_equity is None:
        inferred = infer_initial(Path(args.snapshots))
        start_equity = inferred if inferred is not None else 0.0

    df["pnl"] = df["pnl"].astype(float)
    df["cum_pnl"] = df["pnl"].cumsum()
    df["equity"] = start_equity + df["cum_pnl"]

    preferred_cols = [
        "ts",
        "symbol",
        "pnl",
        "cum_pnl",
        "equity",
        "direction",
        "price",
        "quantity",
        "adapter_latency_ms",
    ]
    equity_cols = [col for col in preferred_cols if col in df.columns]
    ensure_parent(Path(args.out_equity))
    df[equity_cols].to_csv(args.out_equity, index=False)

    daily = df.copy()
    daily["date"] = daily["ts"].dt.date
    grp = daily.groupby("date")["pnl"].agg(["sum", "count"]).rename(columns={"sum": "daily_pnl", "count": "trades"})
    grp["cum_pnl"] = grp["daily_pnl"].cumsum()
    grp["equity"] = start_equity + grp["cum_pnl"]
    grp.reset_index().to_csv(args.out_daily, index=False)


if __name__ == "__main__":
    main()
