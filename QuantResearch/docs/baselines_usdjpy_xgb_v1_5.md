## USDJPY Baseline v1.5 (Single Alpha)

This snapshot freezes the current best-performing configuration before adding V2 multi-model work.

### Config / Model
- Backtest YAML: `QuantResearch/config/usdjpy_xgb_backtest.yaml`
- Live YAML: `QuantTrader/config/usdjpy_xgb.yaml`
- Direction model: `QuantResearch/artifacts/models/usdjpy_h1_xgb/20251114_142600`
- Vol regime model: `QuantResearch/artifacts/models/usdjpy_vol_regime/20251115_165544`
- Walk-forward config: train 6000 bars / test 1500 bars / step 1500 bars (16 windows)

### Key Runs
- Backtest: `Q Research/results/20251115_165558/summary.json`
  - Ann. return `0.105%`, ann. vol `0.074%`, Sharpe `1.42`, MaxDD `-0.16%`, trades `572`
- Walk-forward: `results/walkforward_usdjpy_xgb_regime_v2_20251115_165630/summary.json`
  - Pass windows `9/16`, Sharpe median `1.29`, MaxDD median `-0.025%`

### Strategy Stack
1. `regime_vol_ml` (state-only) → writes `{vol_regime, vol_regime_proba}`
2. `xgb_signal` (long-only)
   - Base thresholds: `prob_long=0.64`, `prob_exit=0.50`, `cooldown=8`, `size_mult=1.0`
   - High-vol adjustments: `prob_long -0.02`, `size_mult 1.2`, `cooldown 6`
   - Low-vol adjustments: `prob_long +0.03`, `size_mult 0.6`, `cooldown 12`

### Monitoring
- Primary dashboard: `monitoring/grafana/risk_metrics_dashboard.json` (existing execution/risk panels)
- Vol regime dashboard: `monitoring/grafana/vol_regime_overview.json` (PnL/Sharpe by regime; expects Prom metrics `strategy_vol_regime_pnl` & `strategy_vol_regime_sharpe`)

### Usage
```bash
# Backtest
PYTHONPATH=QuantResearch .venv/bin/python "Q Research/scripts/backtest_strategy.py" \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml

# Walk-forward
PYTHONPATH=QuantResearch .venv/bin/python "Q Research/scripts/run_walkforward.py" \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml \
  --train-bars 6000 --test-bars 1500 --step-bars 1500 \
  --label usdjpy_xgb_regime_v2
```

Keep this document updated when promoting new models/configs so we always have a reproducible single-alpha baseline for comparison.
