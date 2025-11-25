#!/usr/bin/env python3
"""Unified FX ingest -> clean Parquet + manifest update."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys

import pandas as pd

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = RESEARCH_ROOT.parent
DATA_DIR = RESEARCH_ROOT / "data"
DEFAULT_MANIFEST = DATA_DIR / "_manifest.json"
DEFAULT_REPORT_DIR = DATA_DIR / "clean" / "reports"
DEFAULT_CLEAN_DIR = DATA_DIR / "clean"
INGEST_VERSION = "v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean FX CSV into Parquet + manifest entry.")
    parser.add_argument("--symbol", required=True, help="Symbol label, e.g. USDJPY")
    parser.add_argument("--input", type=Path, required=True, help="Raw CSV path")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output Parquet path (default data/clean/<symbol>_H1_clean.parquet)",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    parser.add_argument("--max-fill-hours", type=int, default=6)
    parser.add_argument("--return-z", type=float, default=6.0)
    parser.add_argument("--volume-z", type=float, default=6.0)
    return parser.parse_args()


def _import_clean_helpers():
    if str(RESEARCH_ROOT) not in sys.path:
        sys.path.insert(0, str(RESEARCH_ROOT))
    from scripts.build_clean_fx_dataset import (
        _ensure_timezone,
        clean_dataset,
        save_report,
    )

    return _ensure_timezone, clean_dataset, save_report


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def update_manifest(manifest_path: Path, entry: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError:
                data = []
    else:
        data = []
    filtered = [item for item in data if item.get("path") != entry["path"]]
    filtered.append(entry)
    filtered.sort(key=lambda x: x.get("path"))
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(filtered, fh, indent=2, ensure_ascii=False)


def main() -> None:
    args = parse_args()
    ensure_timezone, clean_dataset, save_report = _import_clean_helpers()

    if not args.output:
        args.output = DEFAULT_CLEAN_DIR / f"{args.symbol.upper()}_H1_clean.parquet"
    if not args.report:
        args.report = DEFAULT_REPORT_DIR / f"{args.symbol.upper()}_H1_clean_report.json"

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    df_raw = pd.read_csv(args.input)
    if "time" not in df_raw.columns:
        raise SystemExit("Input CSV missing 'time' column")

    df_raw = ensure_timezone(df_raw)
    cleaned, stats = clean_dataset(df_raw, args.max_fill_hours, args.return_z, args.volume_z)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(args.output, index=False)

    if args.report:
        save_report(stats, args.report, cleaned_rows=len(cleaned), symbol=args.symbol)

    entry = {
        "path": _relpath(args.output),
        "rows": int(len(cleaned)),
        "cols": list(cleaned.columns),
        "start_ts": cleaned["time"].min().isoformat() if "time" in cleaned else None,
        "end_ts": cleaned["time"].max().isoformat() if "time" in cleaned else None,
        "sha256": _sha256(args.output),
        "source": _relpath(args.input),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ingest_version": INGEST_VERSION,
        "report": _relpath(args.report) if args.report else None,
    }
    update_manifest(args.manifest, entry)

    print(f"[ingest] symbol={args.symbol} rows={len(cleaned)} output={args.output}")
    print(f"[ingest] manifest updated: {args.manifest}")


if __name__ == "__main__":
    main()
