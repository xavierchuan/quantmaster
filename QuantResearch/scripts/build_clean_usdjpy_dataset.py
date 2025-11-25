#!/usr/bin/env python3
"""
Build a cleaned USDJPY H1 dataset (v2) with weekend/holiday removal,
controlled interpolation for short gaps, outlier diagnostics, and optional
feature export.

Usage:
    python QuantResearch/scripts/build_clean_usdjpy_dataset.py \
        --input QuantResearch/data/raw/USDJPY_H1_full.csv \
        --output-clean QuantResearch/data/clean/USDJPY_H1_clean_v2.csv \
        --output-features QuantResearch/data/clean/USDJPY_H1_with_features.csv
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import List, Optional

import numpy as np
import pandas as pd


RESEARCH_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = RESEARCH_ROOT.parent
sys.path.append(str(RESEARCH_ROOT))
DATA_DIR = RESEARCH_ROOT / "data"
DEFAULT_INPUT = DATA_DIR / "raw" / "USDJPY_H1_5y.csv"
DEFAULT_CLEAN = DATA_DIR / "clean" / "USDJPY_H1_clean_v2.csv"
DEFAULT_FEATURES = DATA_DIR / "clean" / "USDJPY_H1_with_features.csv"
FEATURE_REPORT = DATA_DIR / "clean" / "USDJPY_H1_clean_v2_report.json"

# Maximum gap (in hours) that we will interpolate over. Larger gaps (weekends/holidays)
# are treated as real jumps and left intact.
MAX_FILL_HOURS = 6
RETURN_Z_MAX = 6.0
VOLUME_Z_MAX = 6.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean USDJPY H1 dataset and export features.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Raw USDJPY CSV path.")
    parser.add_argument("--output-clean", type=Path, default=DEFAULT_CLEAN, help="Output CSV for cleaned bars.")
    parser.add_argument("--output-features", type=Path, default=DEFAULT_FEATURES, help="Optional output CSV with engineered features.")
    parser.add_argument("--max-fill-hours", type=int, default=MAX_FILL_HOURS, help="Max consecutive missing hours to interpolate (default: 6).")
    parser.add_argument("--return-z", type=float, default=RETURN_Z_MAX, help="Z-score threshold for price-change outliers.")
    parser.add_argument("--volume-z", type=float, default=VOLUME_Z_MAX, help="Z-score threshold for volume outliers.")
    parser.add_argument("--symbol", default="USDJPY")
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
    # increment segment id whenever we see a large gap
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

    for seg_value, seg in df.groupby(segment_id):
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

    # Basic sanity checks for OHLC relationships
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


def save_report(clean_stats: CleanStats, output: Path, feature_cols: Optional[List[str]] = None, cleaned_rows: int = 0) -> None:
    report = {
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


def export_features(df: pd.DataFrame, output: Path) -> List[str]:
    import importlib.util

    module_path = RESEARCH_ROOT / "scripts" / "train_xgb_usdjpy.py"
    spec = importlib.util.spec_from_file_location("train_xgb_usdjpy", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Unable to load train_xgb_usdjpy.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build_features = getattr(module, "build_features")
    df_feat, feat_cols = build_features(df.copy())
    df_feat.to_csv(output, index=False)
    return feat_cols


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
        feat_cols = export_features(cleaned[["time", "open", "high", "low", "close", "volume"]], args.output_features)

    save_report(stats, FEATURE_REPORT, feature_cols=feat_cols, cleaned_rows=len(cleaned))
    print(f"[clean] rows={len(cleaned)} filled={stats.filled_rows} price_outliers={stats.price_outliers} volume_outliers={stats.volume_outliers}")
    print(f"[clean] outputs -> clean: {args.output_clean}, features: {args.output_features}")


if __name__ == "__main__":
    main()
