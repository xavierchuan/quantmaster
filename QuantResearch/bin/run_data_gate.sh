#!/usr/bin/env bash
# Run data manifest + integrity checks in one go (cron/CI friendly).
#
# Usage:
#   ./bin/run_data_gate.sh
#   LOG_DIR=/tmp ./bin/run_data_gate.sh
#
# It will:
#   1) Rebuild data/_manifest.json for raw/clean/derived
#   2) Run signature validation + data validation via scripts/check_data_integrity.py
#   3) Aggregate data quality summary for quick inspection

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/data_gate.log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

echo "[$(ts)] Starting data gate..." | tee -a "$LOG_FILE"

echo "[$(ts)] Building manifest (data/raw, data/clean, data/derived)..." | tee -a "$LOG_FILE"
python scripts/build_dataset_manifest.py --roots data/raw data/clean data/derived --out data/_manifest.json >> "$LOG_FILE" 2>&1

echo "[$(ts)] Running integrity checks (hash + validation)..." | tee -a "$LOG_FILE"
python scripts/check_data_integrity.py >> "$LOG_FILE" 2>&1

echo "[$(ts)] Aggregating data quality summary..." | tee -a "$LOG_FILE"
python scripts/aggregate_data_quality.py --print >> "$LOG_FILE" 2>&1 || true

echo "[$(ts)] Data gate completed." | tee -a "$LOG_FILE"
