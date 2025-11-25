## USDJPY ML Pipeline Overview (V2 Baseline)

```
OANDA Raw H1 CSV
        ↓  (QuantResearch/data/raw/USDJPY_H1_full.csv)
Clean + Feature Build (scripts/build_clean_usdjpy_dataset.py)
        ↓  (QuantResearch/data/clean/USDJPY_H1_clean_v2.csv)
Feature Engineering (ret/vol/sma_diff/rsi/atr_norm + time cyclical)
        ↓
Model D (XGB Binary, H=6)
        ↓ probabilities
Vol Regime (xgboost multi-class) + StrategyEngine (xgb_signal)
        ↓ actions
Execution/Risk (ATR SL/TP, cooldown, max DD guard)
        ↓
Backtest + Walk-forward + Stress
```

### Components
- **Data**: `QuantResearch/data/clean/USDJPY_H1_clean_v2.csv` + feature snapshot `data/clean/USDJPY_H1_with_features.csv` validated via `scripts/validate_dataset.py`.
- **Feature set**: `{ret_1, ret_3, ret_6, vol_24, sma_diff, rsi, atr_norm, hour/dow sin/cos}` + realized/ATR stats reused by the vol-regime model.
- **Models**:
  - Directional XGB (`train_xgb_usdjpy.py`) → `QuantResearch/artifacts/models/usdjpy_h1_xgb_v2/20251116_134730`.
  - Vol regime classifier (`train_vol_regime_usdjpy.py`) → `QuantResearch/artifacts/models/usdjpy_vol_regime_v2/20251116_134752`.
- **Strategy**: `QuantResearch/strategies/xgb_signal.py` (prob_long=0.64, prob_exit=0.50, cooldown=8, long-only) blended with vol regime adjustments.
- **Engine**: `StrategyEngine` applies costs, ATR exits, risk controls.
- **Evaluation**:
  - Backtest run `QuantResearch/results/20251116_134840/summary.json` (`retention=baseline`).
  - Walk-forward run `results/walkforward_usdjpy_xgb_v2_20251116_134927/walkforward/summary.json` (`retention=wf_baseline`).
- **Monitoring**: Metrics exported via Prometheus + Grafana dashboards (execution, risk, walk-forward stats).

The legacy v1 runs/models remain available only through the archive flow described in `docs/retention.md`. Update this overview whenever a new clean dataset or baseline replaces v2 so downstream docs and cleanup scripts stay accurate.
