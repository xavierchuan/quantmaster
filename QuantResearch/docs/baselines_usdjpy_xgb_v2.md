## USDJPY XGB Baseline (v2)

- **Dataset**: `QuantResearch/data/clean/USDJPY_H1_clean_v2.csv` (built via `scripts/build_clean_usdjpy_dataset.py`, removes weekend/holiday gaps, interpolates ≤6h outages, flags outliers).
- **Feature snapshot**: `QuantResearch/data/clean/USDJPY_H1_with_features.csv` (same engineered fields as the training script).
- **XGB model**: `QuantResearch/artifacts/models/usdjpy_h1_xgb_v2/20251116_134730` (`usdjpy_h1_xgb_latest.json` already points here).
- **Vol regime model**: `QuantResearch/artifacts/models/usdjpy_vol_regime_v2/20251116_134752` (pointer `usdjpy_vol_regime_latest.json` updated).
- **Config**: `QuantResearch/config/usdjpy_xgb_backtest.yaml` (CSV now points to the clean dataset and mirrors live sizing).
- **Status**: _Long-only v2 final (frozen after run `20251116_181739` + WF `walkforward_usdjpy_xgb_v2_final_20251116_181814`)_

### Backtest (full cleaned sample)
- Run: `QuantResearch/results/20251116_181739/summary.json`
- Metrics:
  - Final equity `964,392`
  - Annual return `0.09%`
  - Annual vol `0.07%`
  - Sharpe `1.37`
  - Max drawdown `-0.10%`
  - Trades `154`

### Walk-forward (16 windows, train 6000 / test 1500 / step 1500)
- Run: `results/walkforward_usdjpy_xgb_v2_final_20251116_181814/walkforward/summary.json`
- Metrics:
  - Pass windows `9 / 16` (Sharpe ≥ 1, MaxDD ≤ 10%)
  - Sharpe median `1.24` (mean `1.15`, p05 `-2.42`, p95 `4.16`)
  - MaxDD median `-0.015%` (mean `-0.019%`)

### Reproduce
```bash
# Build clean dataset + features
python QuantResearch/scripts/build_clean_usdjpy_dataset.py \
  --input QuantResearch/data/raw/USDJPY_H1_full.csv \
  --output-clean QuantResearch/data/clean/USDJPY_H1_clean_v2.csv \
  --output-features QuantResearch/data/clean/USDJPY_H1_with_features.csv

# Train XGB on the clean dataset
PYTHONPATH=QuantResearch .venv/bin/python QuantResearch/scripts/train_xgb_usdjpy.py \
  --csv QuantResearch/data/clean/USDJPY_H1_clean_v2.csv \
  --out QuantResearch/artifacts/models/usdjpy_h1_xgb_v2

# Train vol regime classifier
PYTHONPATH=QuantResearch .venv/bin/python QuantResearch/scripts/train_vol_regime_usdjpy.py \
  --csv QuantResearch/data/clean/USDJPY_H1_clean_v2.csv \
  --out QuantResearch/artifacts/models/usdjpy_vol_regime_v2

# Backtest sanity check
PYTHONPATH=QuantResearch .venv/bin/python QuantResearch/scripts/backtest_strategy.py \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml

# Walk-forward gating
PYTHONPATH=QuantResearch .venv/bin/python QuantResearch/scripts/run_walkforward.py \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml \
  --train-bars 6000 --test-bars 1500 --step-bars 1500 \
  --label usdjpy_xgb_v2
```

Keep this document in sync whenever a new clean dataset or model drop replaces the v2 baseline.

---

## Phase 2 – Short-leg experimentation

- **Config**: `QuantResearch/config/usdjpy_xgb_backtest_short.yaml`
- **Model pointer**: `QuantResearch/artifacts/models/usdjpy_h1_xgb_short_latest.json` (multi-class H6 classifier)
- **Status**: _short_v1c (size/threshold tweak A+B)_

### Backtest (full sample, long+short)
- Run: `QuantResearch/results/20251116_223957/summary.json`
- Metrics:
  - Final equity `968,367`
  - Annual return `0.17%`
  - Annual vol `0.09%`
  - Sharpe `1.97`
  - Max drawdown `-0.089%`
  - Trades `264`

### Walk-forward (16 windows, label `usdjpy_xgb_short_v1e`)
- Run: `QuantResearch/results/walkforward_usdjpy_xgb_short_v1e_20251116_224027/walkforward/summary.json`
- Metrics:
  - Pass windows `11 / 16`
  - Sharpe median `2.39` (mean `1.82`)
  - MaxDD median `-0.029%` (mean `-0.030%`)

### Reproduce Phase 2 short run
```bash
# Backtest
PYTHONPATH=QuantResearch .venv/bin/python QuantResearch/scripts/backtest_strategy.py \
  --config QuantResearch/config/usdjpy_xgb_backtest_short.yaml

# Walk-forward (16 windows)
PYTHONPATH=QuantResearch .venv/bin/python QuantResearch/scripts/run_walkforward.py \
  --config QuantResearch/config/usdjpy_xgb_backtest_short.yaml \
  --train-bars 6000 --test-bars 1500 --step-bars 1500 \
  --label usdjpy_xgb_short_v1e
```
