## USDJPY XGB Baseline (v1)

- ⚠️ **Archive only**：V2 现为正式基准（参见 `docs/baselines_usdjpy_xgb_v2.md`）。V1 run/model 仅作为历史对照保存在 `docs/retention.md` 列出的 archive 路径，需要时请先从 archive 解包到 `QuantResearch/results/` 与 `QuantResearch/artifacts/models/` 再引用。

- **Backtest config**: `QuantResearch/config/usdjpy_xgb_backtest.yaml`  
- **Live config**: `QuantTrader/config/usdjpy_xgb.yaml`  
- **Model**: `QuantResearch/artifacts/models/usdjpy_h1_xgb/20251114_142600` (`*_latest.json` pointer aligned)  
- **Costs**: spread `2.0` pips, slippage `0.3` pips, commission `$0.25/MM`

### Backtest (Full 5y sample)
- Run: `QuantResearch/results/20251114_154855/summary.json`
- Metrics:
  - Annual return `0.105%`
  - Annual vol `0.088%`
  - Sharpe `1.42`
  - Max drawdown `-0.16%`
  - Trades `572`

### Walk-forward (16× rolling windows)
- Run: `results/walkforward_usdjpy_xgb_20251114_153953/walkforward/summary.json`
- Metrics:
  - Pass windows `9 / 16`
  - Sharpe median `1.57` (mean `0.83`, p05 `-2.44`, p95 `3.93`)
  - MaxDD median `-0.026%`

### Reproduce
```bash
# Backtest
.venv/bin/python QuantResearch/scripts/backtest_strategy.py \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml

# Walk-forward
.venv/bin/python QuantResearch/scripts/run_walkforward.py \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml \
  --train-bars 6000 --test-bars 1500 --step-bars 1500 \
  --label usdjpy_xgb
```

### Notes
- Params: `prob_long=0.64`, `prob_exit=0.50`, `cooldown_bars=8`, long-only.
- Use this baseline for all future comparisons (record run IDs alongside new experiments).
