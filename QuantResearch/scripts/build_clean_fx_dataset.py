#!/usr/bin/env python3
"""
Generic FX H1 cleaner (v2) used to produce baseline clean datasets per pair.
Copies the USDJPY cleaning logic, parameterized by input/output paths.

Example:
  python QuantResearch/scripts/build_clean_fx_dataset.py \
    --input QuantResearch/data/raw/EURUSD_H1_5y.csv \
    --output-clean QuantResearch/data/clean/EURUSD_H1_clean_v2.csv
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = RESEARCH_ROOT / "data"
DEFAULT_FEATURES = None
DEFAULT_REPORT = None

# Max gap (hours) to interpolate; larger gaps remain as is
MAX_FILL_HOURS = 6
RETURN_Z_MAX = 6.0
VOLUME_Z_MAX = 6.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean FX H1 dataset and export features (optional).")
    parser.add_argument("--input", type=Path, required=True, help="Raw FX CSV path (time,open,high,low,close,volume).")
    parser.add_argument("--output-clean", type=Path, required=True, help="Output CSV for cleaned bars.")
    parser.add_argument("--output-features", type=Path, default=DEFAULT_FEATURES, help="Optional output CSV with engineered features.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Optional json report path.")
    parser.add_argument("--max-fill-hours", type=int, default=MAX_FILL_HOURS, help="Max consecutive missing hours to interpolate (default: 6).")
    parser.add_argument("--return-z", type=float, default=RETURN_Z_MAX, help="Z-score threshold for price-change outliers.")
    parser.add_argument("--volume-z", type=float, default=VOLUME_Z_MAX, help="Z-score threshold for volume outliers.")
    parser.add_argument("--symbol", default=None, help="Symbol label for reporting (optional).")
    return parser.parse_args()


def _ensure_timezone(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").drop_duplicates(subset="time")


def _is_trading_hour(ts: pd.Timestamp) -> bool:
    dow = ts.dayofweek  # Monday=0 ... Sunday=6
    hour = ts.hour
    if dow == 5:  # Saturday
        return False
    if dow == 6 and hour < 22:  # Sunday before 22:00 UTC
        return False
    if dow == 4 and hour > 21:  # Friday after 21:00 UTC
        return False
    return True


def _tag_segments(df: pd.DataFrame, max_fill_hours: int) -> pd.Series:
    gaps = df["time"].diff().dt.total_seconds().div(3600).fillna(0.0)
    return gaps.gt(max_fill_hours).cumsum()


@dataclass
class CleanStats:
    total_rows: int = 0
    removed_weekend: int = 0
    filled_rows: int = 0
    segments: int = 0
    price_outliers: int = 0
    volume_outliers: int = 0


def clean_dataset(df: pd.DataFrame, max_fill_hours: int, return_z: float, volume_z: float) -> tuple[pd.DataFrame, CleanStats]:
    stats = CleanStats(total_rows=len(df))
    df = df[["time", "open", "high", "low", "close", "volume"]].copy()
    before = len(df)
    mask = df["time"].map(_is_trading_hour)
    df = df[mask]
    stats.removed_weekend = before - len(df)

    segment_id = _tag_segments(df, max_fill_hours)
    stats.segments = int(segment_id.max() + 1)
    segments: List[pd.DataFrame] = []
    filled_indices: List[pd.Timestamp] = []

    for _, seg in df.groupby(segment_id):
        seg = seg.set_index("time").sort_index()
        if seg.empty:
            continue
        idx = pd.date_range(seg.index.min(), seg.index.max(), freq="h")
        seg = seg.reindex(idx)
        filled_mask = seg["open"].isna()
        filled_indices.extend(seg.index[filled_mask])
        seg.interpolate(method="time", inplace=True, limit_direction="both")
        segments.append(seg)

    if not segments:
        raise SystemExit("No data retained after cleaning. Check input file or filters.")

    combined = pd.concat(segments).reset_index().rename(columns={"index": "time"})
    time_dt = combined["time"].dt
    if time_dt.tz is None:
        combined["time"] = time_dt.tz_localize("UTC")
    else:
        combined["time"] = time_dt.tz_convert("UTC")
    combined["was_filled"] = combined["time"].isin(filled_indices)
    combined.sort_values("time", inplace=True)
    stats.filled_rows = int(combined["was_filled"].sum())

    # Basic OHLC sanity
    combined["high"] = combined[["high", "open", "close"]].max(axis=1)
    combined["low"] = combined[["low", "open", "close"]].min(axis=1)

    returns = combined["close"].pct_change()
    ret_z = (returns - returns.mean()) / returns.std(ddof=1)
    price_outliers = ret_z.abs() > return_z
    stats.price_outliers = int(price_outliers.sum())
    combined["is_price_outlier"] = price_outliers.fillna(False)

    vol = combined["volume"].replace(0, np.nan)
    vol_z = (np.log(vol) - np.log(vol).mean()) / np.log(vol).std(ddof=1)
    volume_outliers = vol_z.abs() > volume_z
    stats.volume_outliers = int(volume_outliers.fillna(False).sum())
    combined["is_volume_outlier"] = volume_outliers.fillna(False)

    combined["segment_id"] = _tag_segments(combined, max_fill_hours)
    combined["is_gap_reopen"] = combined["segment_id"].diff().ne(0).fillna(True)
    return combined, stats


def save_report(clean_stats: CleanStats, output: Path, feature_cols: Optional[List[str]] = None, cleaned_rows: int = 0, symbol: str | None = None) -> None:
    report = {
        "symbol": symbol,
        "total_rows_raw": clean_stats.total_rows,
        "clean_rows": cleaned_rows,
        "removed_weekend": clean_stats.removed_weekend,
        "filled_short_gaps": clean_stats.filled_rows,
        "segments": clean_stats.segments,
        "price_outliers": clean_stats.price_outliers,
        "volume_outliers": clean_stats.volume_outliers,
        "feature_columns": feature_cols or [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")
    df = pd.read_csv(args.input)
    if "time" not in df.columns:
        raise SystemExit("Input CSV missing 'time' column.")

    df = _ensure_timezone(df)
    cleaned, stats = clean_dataset(df, args.max_fill_hours, args.return_z, args.volume_z)

    args.output_clean.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_csv(args.output_clean, index=False)

    feat_cols: List[str] = []
    if args.output_features:
        # Placeholder: feature export not wired for generic pairs
        pass

    if args.report:
        save_report(stats, args.report, feature_cols=feat_cols, cleaned_rows=len(cleaned), symbol=args.symbol)

    print(f"[clean] symbol={args.symbol or ''} rows={len(cleaned)} filled={stats.filled_rows} price_outliers={stats.price_outliers} volume_outliers={stats.volume_outliers}")
    print(f"[clean] outputs -> clean: {args.output_clean}, features: {args.output_features}")


if __name__ == "__main__":
    main()
