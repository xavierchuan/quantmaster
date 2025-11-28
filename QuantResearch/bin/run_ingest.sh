#!/usr/bin/env bash
# Cron-friendly wrapper for scheduled OANDA ingestion.
#
# Example crontab (UTC):
#   5 0 * * * /path/to/QuantResearch/bin/run_ingest.sh >> /path/to/QuantResearch/logs/cron_ingest.log 2>&1
#
# Required env:
#   export OANDA_TOKEN="..."

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "[$TS] Starting scheduled ingest..." | tee -a "$LOG_DIR/ingest_cron.log"

python scripts/ingest_oanda.py --schedule config/ingest_schedule.yaml >> "$LOG_DIR/ingest_cron.log" 2>&1

EXIT_CODE=$?
TS_END=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
if [ $EXIT_CODE -eq 0 ]; then
  echo "[$TS_END] Ingest completed successfully. Building manifest..." | tee -a "$LOG_DIR/ingest_cron.log"
  python scripts/build_dataset_manifest.py --dirs data/raw data/clean data/derived --output data/_manifest.json >> "$LOG_DIR/ingest_cron.log" 2>&1
  echo "[$TS_END] Manifest updated at data/_manifest.json. Aggregating DQ summary..." | tee -a "$LOG_DIR/ingest_cron.log"
  python scripts/aggregate_data_quality.py --print >> "$LOG_DIR/ingest_cron.log" 2>&1 || true
  echo "[$TS_END] Ingest + manifest pipeline finished." | tee -a "$LOG_DIR/ingest_cron.log"
else
  echo "[$TS_END] Ingest failed with code $EXIT_CODE" | tee -a "$LOG_DIR/ingest_cron.log"
fi

exit $EXIT_CODE
