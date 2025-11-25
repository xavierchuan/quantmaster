#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PAPER=$ROOT/results/execution/paper/fills.csv
LIVE=$ROOT/results/execution/live/fills.csv
TCA_OUT=$ROOT/results/execution/tca_summary.json
RUN_ID=${1:-$(date -u +"%Y%m%d_%H%M%S")}
if [ ! -s "$PAPER" ] || [ ! -s "$LIVE" ]; then
  echo "Missing fills CSVs: $PAPER or $LIVE" >&2
  exit 1
fi
cd "$ROOT"
python ../QuantResearch/scripts/compare_fills.py --paper "$PAPER" --live "$LIVE" --out "$TCA_OUT"
python ../QuantResearch/scripts/update_metrics_from_tca.py \
  --tca "$TCA_OUT" \
  --metrics ../QuantResearch/results/risk/metrics.csv \
  --run-id "$RUN_ID" \
  --status pass \
  --latency-avg ${LATENCY_AVG:-30} \
  --latency-p95 ${LATENCY_P95:-45} \
  --total-pnl ${TOTAL_PNL:-0} \
  --max-exposure ${MAX_EXPOSURE:-500000} \
  --max-drawdown ${MAX_DRAWDOWN:-0.05} \
  --rolling-sharpe ${ROLLING_SHARPE:-1.4} \
  --live-drawdown ${LIVE_DRAWDOWN:-0.05} \
  --live-latency-p95 ${LIVE_LATENCY_P95:-45} \
  --slippage-bps ${SLIPPAGE_BPS:-2}
# Capture real account state for audit/monitoring
python scripts/export_account_snapshot.py \
  --out results/execution/account_snapshots.csv
python scripts/export_live_equity.py \
  --fills results/execution/live/fills.csv \
  --snapshots results/execution/account_snapshots.csv \
  --out-equity results/execution/live_equity.csv \
  --out-daily results/execution/daily_pnl.csv
if [ -n "${PUSHGATEWAY_URL:-}" ]; then
  python scripts/export_account_metrics_prom.py \
    --csv results/execution/account_snapshots.csv \
    --job account_state | \
  curl --data-binary @- "$PUSHGATEWAY_URL/metrics/job/account_state"
fi
cd ../QuantResearch
python scripts/watch_ops_metrics.py
source ../.env.demo && python scripts/export_metrics_prom.py --csv results/risk/metrics.csv --job risk_sim | curl --data-binary @- "$PUSHGATEWAY_URL/metrics/job/risk_sim"
