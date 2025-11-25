# results/<run_id>/metrics.json Schema

```json
{
  "fast": 50,
  "slow": 200,
  "final_equity": 100347.30,
  "ann_return": 0.0034,
  "ann_vol": 0.0089,
  "sharpe": 0.38,
  "sortino": 0.52,
  "calmar": 0.62,
  "max_drawdown": -0.0063,
  "max_drawdown_duration_bars": 48,
  "avg_drawdown_duration_bars": 12.5,
  "current_drawdown_duration_bars": 0,
  "recovery_time_bars": 48,
  "trades": 148,
  "atr_sl": null,
  "atr_tp": null,
  "atr_window": 14,
  "data_report": "data/outputs/stats/data_reports/data_EURUSD_H1_50x200_SL50.0xTPNone_SHORT.json",
  "data_validation": {
    "severity": "warn",
    "messages": [
      "Non-zero timestamp gaps detected (ratio=0.0087).",
      "Detected numeric outliers (threshold z>5.0)."
    ]
  }
}
```

- `final_equity`: Ending cash/equity.
- `ann_return`, `ann_vol`, `sharpe`, `sortino`, `calmar`, `max_drawdown`: Derived from `metrics/perf.py`。
- `max_drawdown_duration_bars` / `avg_drawdown_duration_bars` / `current_drawdown_duration_bars` / `recovery_time_bars`: 回撤持续时间指标（单位=bar）。
- `trades`: Total trade count.
- `atr_*`: ATR configuration snapshot，便于批量分析。
- `data_report`: 相对路径到原始数据校验报告。
- `data_validation`: 摘要（severity + messages）供 CI/仪表读取。
