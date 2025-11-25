#!/usr/bin/env python3
"""
Scan data directories and emit a manifest with basic metadata and checksums.

Outputs a JSON manifest (default: data/_manifest.json) with entries:
[
  {
    "path": "data/clean/GBPUSD_H1_clean_v2_with_regime.csv",
    "rows": 30930,
    "cols": ["time","open","high","low","close",...],
    "start_ts": "2016-01-01T00:00:00+00:00",
    "end_ts": "2025-11-20T23:00:00+00:00",
    "sha256": "abc123...",
    "size_bytes": 1234567
  },
  ...
]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def summarize_csv(path: Path, time_col: str = "time") -> Dict:
    # Read minimally: only time column if present, otherwise head/tail
    cols: Optional[List[str]] = None
    start_ts = end_ts = None
    try:
        df_time = pd.read_csv(path, usecols=[time_col])
        start_ts = pd.to_datetime(df_time[time_col].iloc[0], utc=True).isoformat()
        end_ts = pd.to_datetime(df_time[time_col].iloc[-1], utc=True).isoformat()
        cols = [time_col]
        rows = len(df_time)
    except Exception:
        try:
            df_sample = pd.read_csv(path, nrows=5)
            cols = df_sample.columns.tolist()
            rows = sum(1 for _ in path.open("r", encoding="utf-8"))
        except Exception:
            cols = None
            rows = None

    size_bytes = path.stat().st_size
    sha = hash_file(path)
    return {
        "path": str(path),
        "rows": rows,
        "cols": cols,
        "start_ts": start_ts,
        "end_ts": end_ts,
        "sha256": sha,
        "size_bytes": size_bytes,
    }


def scan_dirs(roots: List[Path], time_col: str) -> List[Dict]:
    entries: List[Dict] = []
    for root in roots:
        if not root.exists():
            logger.warning(f"Root not found: {root}")
            continue
        for path in root.rglob("*.csv"):
            if path.is_file():
                logger.info(f"Summarizing {path}")
                entries.append(summarize_csv(path, time_col=time_col))
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description="Build dataset manifest.")
    ap.add_argument(
        "--roots",
        nargs="+",
        default=["QuantResearch/data/clean", "QuantResearch/data/raw", "QuantResearch/data/derived"],
        help="Directories to scan for CSV files",
    )
    ap.add_argument("--time-col", default="time", help="Time column name to detect start/end")
    ap.add_argument(
        "--out",
        default="QuantResearch/data/_manifest.json",
        help="Output manifest path",
    )
    args = ap.parse_args()

    roots = [Path(r).expanduser() for r in args.roots]
    entries = scan_dirs(roots, time_col=args.time_col)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
    logger.info("Wrote manifest with %d entries to %s", len(entries), out_path)


if __name__ == "__main__":
    main()
