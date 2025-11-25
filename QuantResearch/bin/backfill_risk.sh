#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

MISSING=$(
python - <<'PY'
from pathlib import Path
import csv
results_dir = Path("results")
runs = sorted(p.name for p in results_dir.iterdir() if p.is_dir() and p.name[:4].isdigit())
recorded = set()
metrics = Path("results/risk/metrics.csv")
if metrics.exists():
    with metrics.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            recorded.add(row["run_id"])
todo = [r for r in runs if r not in recorded]
print(",".join(todo))
PY
)

if [ -z "$MISSING" ]; then
  echo "No runs to backfill."
  exit 0
fi

echo "Backfilling runs: $MISSING"
python scripts/backfill_risk_metrics.py --runs "$MISSING"
