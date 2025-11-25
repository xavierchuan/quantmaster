"""Fetch OANDA account summary and append to CSV/Prometheus."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import os
import sys

TRADER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(TRADER_ROOT)
sys.path.extend([TRADER_ROOT, REPO_ROOT])

from shared.utils.oanda_client import snapshot_account


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Export OANDA account snapshot to CSV")
    ap.add_argument("--out", default="QuantTrader/results/execution/account_snapshots.csv")
    return ap.parse_args()


def append_csv(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=record.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(record)


def main() -> None:
    args = parse_args()
    record = snapshot_account()
    append_csv(Path(args.out), record)


if __name__ == "__main__":
    main()
