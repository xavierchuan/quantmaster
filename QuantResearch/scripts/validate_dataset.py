#!/usr/bin/env python3
"""
Quick data validation for a single CSV dataset.

Checks:
- duplicate timestamps
- gap ratio (missing bars vs. expected frequency) if freq provided
- NaN ratio per column
- time monotonicity

Outputs a JSON report (or stdout) with severity = ok/warn/error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parent
DEFAULT_MANIFEST = str(BASE_DIR / "data/_manifest.json")

TIME_COLUMNS = ("ts", "time", "timestamp", "datetime", "date")
_MANIFEST_CACHE: dict[Path, list[dict]] = {}

import numpy as np
import pandas as pd
from loguru import logger


def compute_gap_ratio(ts: pd.Series, freq: str) -> float:
    if ts.empty:
        return 0.0
    ts_sorted = ts.sort_values()
    expected = pd.date_range(ts_sorted.iloc[0], ts_sorted.iloc[-1], freq=freq)
    missing = np.setdiff1d(expected.values, ts_sorted.values)
    return len(missing) / len(expected) if len(expected) else 0.0


def load_manifest(manifest_path: Path) -> list[dict]:
    manifest_path = manifest_path.resolve()
    if manifest_path in _MANIFEST_CACHE:
        return _MANIFEST_CACHE[manifest_path]
    if not manifest_path.exists():
        return []
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        records = data.get("files", [])
    else:
        records = data
    _MANIFEST_CACHE[manifest_path] = records
    return records


def load_manifest_entry(manifest_path: Path, csv_path: Path) -> Optional[dict]:
    entries = load_manifest(manifest_path)
    if not entries:
        return None
    abs_path = csv_path.resolve()
    candidates = {
        str(abs_path),
        str(abs_path.relative_to(REPO_ROOT)) if abs_path.is_relative_to(REPO_ROOT) else None,
    }
    # Some manifests prefix with "QuantResearch/"
    try:
        rel_quant = Path("QuantResearch") / abs_path.relative_to(BASE_DIR)
        candidates.add(str(rel_quant))
    except ValueError:
        pass
    try:
        candidates.add(str(abs_path.relative_to(BASE_DIR)))
    except ValueError:
        pass
    candidates = {c for c in candidates if c}
    for entry in entries:
        entry_path = str(entry.get("path"))
        if entry_path in candidates:
            return entry
    return None


def validate(path: Path, time_col: str, freq: str | None) -> Dict:
    df = pd.read_csv(path)
    report: Dict = {"path": str(path), "rows": len(df), "severity": "ok", "issues": []}

    if time_col not in df.columns:
        report["severity"] = "error"
        report["issues"].append(f"time column '{time_col}' missing")
        return report

    ts = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    if ts.isna().any():
        report["severity"] = "error"
        report["issues"].append("time column has non-parseable values")
        return report

    # Monotonic check
    if not ts.is_monotonic_increasing:
        report["severity"] = "warn"
        report["issues"].append("time column not strictly increasing")

    # Duplicates
    dup_ratio = ts.duplicated().mean()
    if dup_ratio > 0:
        sev = "error" if dup_ratio > 0.001 else "warn"
        report["severity"] = max_severity(report["severity"], sev)
        report["issues"].append(f"duplicate timestamp ratio={dup_ratio:.4f}")

    # Gap ratio if freq specified
    if freq:
        gap_ratio = compute_gap_ratio(ts, freq=freq)
        if gap_ratio > 0:
            sev = "error" if gap_ratio > 0.01 else "warn"
            report["severity"] = max_severity(report["severity"], sev)
            report["issues"].append(f"gap ratio={gap_ratio:.4f}")

    # NaN ratio per column
    nan_cols = {}
    for col in df.columns:
        nan_ratio = df[col].isna().mean()
        if nan_ratio > 0:
            nan_cols[col] = nan_ratio
            if nan_ratio > 0.05:
                report["severity"] = max_severity(report["severity"], "warn")
    if nan_cols:
        report["issues"].append(f"nan ratios: {json.dumps(nan_cols)}")

    return report


def compute_report(path: Path, manifest_entry: Optional[dict], z_threshold: float = 5.0) -> Dict:
    df = pd.read_csv(path)
    report: Dict = {
        "path": str(path),
        "severity": "ok",
        "messages": [],
        "duplicate_timestamps": 0,
        "gap_ratio": None,
    }

    time_col = next((c for c in TIME_COLUMNS if c in df.columns), None)
    if time_col:
        ts = pd.to_datetime(df[time_col], utc=True, errors="coerce")
        dup = int(ts.duplicated().sum())
        report["duplicate_timestamps"] = dup
        if dup > 0:
            report["severity"] = max_severity(report["severity"], "warn")
            report["messages"].append(f"duplicate timestamps={dup}")
        diffs = ts.diff().dropna()
        if not diffs.empty:
            median = diffs.median()
            if pd.notna(median) and median.total_seconds() > 0:
                span = (ts.iloc[-1] - ts.iloc[0]).total_seconds()
                expected = int(span / median.total_seconds()) + 1 if span > 0 else len(ts)
                if expected > 0:
                    gap_ratio = max(0.0, (expected - len(ts)) / expected)
                    report["gap_ratio"] = float(gap_ratio)
                    if gap_ratio > 0.01:
                        report["severity"] = max_severity(report["severity"], "warn")
                        report["messages"].append(f"gap ratio={gap_ratio:.4f}")
    else:
        report["messages"].append("no time column detected")

    numeric_cols = df.select_dtypes(include=[np.number]).columns
    outliers = {}
    for col in numeric_cols:
        series = df[col].dropna().astype(float)
        if series.empty:
            continue
        std = series.std(ddof=0)
        if std == 0 or np.isnan(std):
            continue
        z = np.abs((series - series.mean()) / std)
        count = int((z > z_threshold).sum())
        if count:
            outliers[col] = count
    if outliers:
        report["severity"] = max_severity(report["severity"], "warn")
        report["messages"].append(f"outliers detected: {outliers}")

    if manifest_entry:
        expected_rows = manifest_entry.get("rows")
        if expected_rows and abs(expected_rows - len(df)) > 0:
            report["messages"].append(
                f"row count diff expected={expected_rows} actual={len(df)}"
            )
            report["severity"] = max_severity(report["severity"], "warn")
        expected_hash = manifest_entry.get("sha256")
        if expected_hash:
            import hashlib

            actual_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                report["messages"].append("sha256 mismatch vs manifest")
                report["severity"] = max_severity(report["severity"], "warn")

    return report


def max_severity(current: str, new: str) -> str:
    order = {"ok": 0, "warn": 1, "error": 2}
    return new if order[new] > order[current] else current


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate a CSV dataset.")
    ap.add_argument("--path", required=True, help="Path to CSV")
    ap.add_argument("--time-col", default="time", help="Time column name")
    ap.add_argument("--freq", default=None, help="Expected frequency (e.g., 1H, 60min, 1T). Optional.")
    ap.add_argument("--out", default=None, help="Optional JSON report path")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise FileNotFoundError(path)

    report = validate(path, time_col=args.time_col, freq=args.freq)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        logger.info("Report written to %s (severity=%s)", out_path, report["severity"])
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    if report["severity"] == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
