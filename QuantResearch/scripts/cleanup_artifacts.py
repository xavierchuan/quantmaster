#!/usr/bin/env python3
"""
Utility to prune generated artifacts (results + stats/trades) based on the
`retention` metadata stored in each `summary.json`.

Usage:
  python QuantResearch/scripts/cleanup_artifacts.py \
    --results QuantResearch/results \
    --data-root QuantResearch/data/outputs \
    --dry-run --prune-data-outputs

Add `--apply` to actually delete files. Files/directories marked with
`retention` values such as `baseline`, `wf_baseline`, or `archive` are kept.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

SUMMARY_CANDIDATES = ("summary.json", "walkforward/summary.json")
DEFAULT_KEEP = ("baseline", "wf_baseline", "archive")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup generated artifacts safely")
    parser.add_argument(
        "--results",
        default="QuantResearch/results",
        help="Root directory that contains run folders (default: %(default)s)",
    )
    parser.add_argument(
        "--data-root",
        default="QuantResearch/data/outputs",
        help="Root directory for data outputs (stats/trades/equity). Used when pruning dangling files.",
    )
    parser.add_argument(
        "--keep",
        nargs="*",
        default=list(DEFAULT_KEEP),
        help="Retention labels that should never be deleted (default: %(default)s)",
    )
    parser.add_argument(
        "--keep-run",
        action="append",
        default=[],
        help="Specific run directory names to always keep (can be passed multiple times).",
    )
    parser.add_argument(
        "--prune-data-outputs",
        action="store_true",
        help="Delete files under data/outputs/{stats,trades} that are not referenced by kept runs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete files. Without this flag the script only prints a plan.",
    )
    return parser.parse_args()


def find_summary_path(run_dir: Path) -> Optional[Path]:
    for candidate in SUMMARY_CANDIDATES:
        path = run_dir / candidate
        if path.exists():
            return path
    return None


def read_json(path: Path) -> Optional[Dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[WARN] Failed to read {path}: {exc}", file=sys.stderr)
        return None


def gather_artifact_paths(run_dir: Path, project_root: Path) -> Set[Path]:
    paths: Set[Path] = set()
    summary_files = list(run_dir.rglob("summary.json"))
    for summary_path in summary_files:
        data = read_json(summary_path)
        if not data:
            continue
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        for key in ("trades", "trade_stats", "stats", "equity"):
            value = artifacts.get(key)
            if not isinstance(value, str):
                continue
            norm = (project_root / value).resolve() if not Path(value).is_absolute() else Path(value)
            paths.add(norm)
    return paths


def collect_data_output_files(data_root: Path) -> List[Path]:
    files: List[Path] = []
    for sub in ("stats", "trades"):
        root = data_root / sub
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                files.append(path.resolve())
    return files


def main() -> None:
    args = parse_args()
    results_root = Path(args.results).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    project_root = results_root.parent

    if not results_root.exists():
        raise SystemExit(f"Results directory not found: {results_root}")

    keep_labels = set(args.keep)
    keep_runs = set(args.keep_run or [])

    run_plan: List[Dict[str, object]] = []
    kept_runs: List[Path] = []
    kept_artifacts: Set[Path] = set()

    for run_dir in sorted(p for p in results_root.iterdir() if p.is_dir()):
        summary_path = find_summary_path(run_dir)
        if not summary_path:
            continue
        data = read_json(summary_path)
        retention = (data or {}).get("retention", "ephemeral")
        name = run_dir.name
        keep = name in keep_runs or retention in keep_labels
        run_plan.append(
            {
                "name": name,
                "path": run_dir,
                "summary": summary_path,
                "retention": retention,
                "keep": keep,
            }
        )
        if keep:
            kept_runs.append(run_dir)
            kept_artifacts |= gather_artifact_paths(run_dir, project_root)

    runs_to_delete = [entry for entry in run_plan if not entry["keep"]]

    print("=== Run cleanup plan ===")
    if runs_to_delete:
        for entry in runs_to_delete:
            name = entry["name"]
            retention = entry["retention"]
            print(f"[DEL] {name} (retention={retention}) -> {entry['path']}")
            if args.apply:
                shutil.rmtree(entry["path"])  # type: ignore[arg-type]
        if not args.apply:
            print("Dry-run mode, no run directories were removed.")
    else:
        print("No run directories eligible for deletion.")

    if args.prune_data_outputs:
        data_files = collect_data_output_files(data_root)
        deletions: List[Path] = []
        kept_artifacts_norm = {path.resolve() for path in kept_artifacts}
        for file_path in data_files:
            if file_path not in kept_artifacts_norm:
                deletions.append(file_path)
        print("\n=== Data outputs cleanup plan ===")
        if deletions:
            for path in deletions:
                print(f"[DEL] {path}")
                if args.apply:
                    path.unlink(missing_ok=True)  # type: ignore[arg-type]
            if not args.apply:
                print("Dry-run mode, no data output files were removed.")
        else:
            print("No dangling stats/trades files found.")

    if args.apply:
        print("\nCleanup complete.")
    else:
        print("\nDry run complete. Re-run with --apply to delete listed files.")


if __name__ == "__main__":
    main()
