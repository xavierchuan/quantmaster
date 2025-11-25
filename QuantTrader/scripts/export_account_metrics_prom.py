#!/usr/bin/env python3
"""Emit latest account snapshot as Prometheus exposition."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

TRADER_ROOT = Path(__file__).resolve().parents[1]
if str(TRADER_ROOT) not in sys.path:
    sys.path.insert(0, str(TRADER_ROOT))

from utils.risk import DEFAULT_RISK_PROFILE, load_risk_profile


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Export account snapshot metrics to Prom format")
    ap.add_argument("--csv", default="QuantTrader/results/execution/account_snapshots.csv")
    ap.add_argument("--job", default="account_state")
    ap.add_argument(
        "--risk-profile",
        default=str(DEFAULT_RISK_PROFILE),
        help="Risk profile YAML to report risk_scale/max_leverage",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"snapshot CSV not found: {path}")
    try:
        df = pd.read_csv(path, comment="#")
    except pd.errors.ParserError:
        # Attempt strict CSV with on_bad_lines
        df = pd.read_csv(path, on_bad_lines="skip")
    if df.empty:
        raise SystemExit("snapshot CSV is empty")
    latest = df.tail(1).iloc[0]

    def val(key: str, default: float = 0.0) -> float:
        raw = latest.get(key, default)
        try:
            if pd.isna(raw):
                return default
        except TypeError:
            pass
        return float(raw)

    currency = latest.get("currency", "USD") or "USD"
    labels = f'job="{args.job}",currency="{currency}"'
    metrics = {
        "fx_account_balance": val("balance"),
        "fx_account_nav": val("nav"),
        "fx_account_unrealized_pl": val("unrealizedPL"),
        "fx_account_margin_available": val("marginAvailable"),
        "fx_account_margin_used": val("marginUsed"),
    }
    for name, value in metrics.items():
        print(f"{name}{{{labels}}} {value}")

    profile = load_risk_profile(args.risk_profile)
    risk_labels = f'job="{args.job}"'
    print(f"fx_risk_scale{{{risk_labels}}} {float(profile.risk_scale or 1.0)}")
    if profile.max_leverage is not None:
        print(f"fx_account_max_leverage{{{risk_labels}}} {profile.max_leverage}")

    margin_rate = val("marginRate", 0.0)
    if margin_rate:
        print(f"fx_margin_rate{{{labels}}} {margin_rate}")
    gross = val("grossPositionValue", 0.0)
    if gross:
        print(f"fx_gross_position_value{{{labels}}} {gross}")
    leverage = val("effectiveLeverage", 0.0)
    if leverage:
        print(f"fx_account_leverage{{{labels}}} {leverage}")


if __name__ == "__main__":
    main()
