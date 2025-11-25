## Artifact Retention & Cleanup

This document tracks which generated assets must stay in the repo (baseline evidence) and which can be purged through `scripts/cleanup_artifacts.py`. Always update this table **before** running destructive cleanups or rotating baselines.

### Retained runs
| Tag | Path | Retention | Notes |
| --- | --- | --- | --- |
| `usdjpy_xgb_v2_backtest_final` | `QuantResearch/results/20251116_181739` | `baseline` | Frozen long-only v2 metrics referenced by README/docs。Keep `data/outputs` artifacts for reproduction。 |
| `usdjpy_xgb_v2_walkforward_final` | `QuantResearch/results/walkforward_usdjpy_xgb_v2_final_20251116_181814` | `wf_baseline` | Latest 16-window gating run (pass=9). `walkforward/summary.json` tagged for retention。 |
| `usdjpy_xgb_v2_backtest_legacy` | `QuantResearch/results/20251116_134840` | `archive` | Previous aggressive baseline kept for comparison only。 |
| `usdjpy_xgb_v2_walkforward_legacy` | `QuantResearch/results/walkforward_usdjpy_xgb_v2_20251116_134927` | `archive` | Legacy WF referenced by older docs。 |
| `usdjpy_xgb_v1_walkforward` | `results/walkforward_usdjpy_xgb_20251114_153953` | `archive` | Legacy comparison kept outside `QuantResearch` tree; restore only when auditing regressions. |
| `usdjpy_xgb_short_v1c_backtest` | `QuantResearch/results/20251116_221239` | `archive_phase2` | Previous A+B tweak snapshot (kept for reference). |
| `usdjpy_xgb_short_v1c_walkforward` | `QuantResearch/results/walkforward_usdjpy_xgb_short_v1c_20251116_221304` | `archive_phase2` | Legacy WF supporting the v1c config. |
| `usdjpy_xgb_short_v1e_backtest` | `QuantResearch/results/20251116_223957` | `phase2_short` | Current candidate baseline after vol-aware short tuning + trend filter. |
| `usdjpy_xgb_short_v1e_walkforward` | `QuantResearch/results/walkforward_usdjpy_xgb_short_v1e_20251116_224027` | `phase2_short` | 16-window WF (pass=11/16, higher Sharpe median) used for gating latest short setup. |
| `usdjpy_xgb_short_v1l_backtest` | `QuantResearch/results/20251117_101113` | `phase2_short` | Latest relaxed short config; ann=1.02%, Sharpe=3.23, MaxDD=-0.37%, trades=1302. |
| `usdjpy_xgb_short_v1l_walkforward` | `results/walkforward_short_v1l_20251117_101206` | `phase2_short` | WF pass=13/16; Sharpe mean=4.49, ann mean=1.62%, MaxDD mean=-0.075%. |

### Retained models
| Model | Path | Retention | Notes |
| --- | --- | --- | --- |
| XGB Directional v2 | `QuantResearch/artifacts/models/usdjpy_h1_xgb_v2/20251116_134730` | `baseline` | `usdjpy_h1_xgb_latest.json` points here. |
| Vol Regime v2 | `QuantResearch/artifacts/models/usdjpy_vol_regime_v2/20251116_134752` | `baseline` | `usdjpy_vol_regime_latest.json` points here. |
| XGB Directional v1 | `QuantResearch/artifacts/models/usdjpy_h1_xgb/20251114_142600` | `archive` | Only keep in `archive/models/` tarball; remove from working tree once tar exists. |

### Operational folders (always keep)
These directories host shared diagnostics that do not contain per-run `summary.json` files, so the cleanup script skips them automatically. Documenting them here avoids accidental deletions.

| Path | Retention | Notes |
| --- | --- | --- |
| `QuantResearch/results/data_quality` | `system` | Latest dataset validation reports referenced by runbooks. |
| `QuantResearch/results/execution` | `system` | Paper/risk replay outputs (fills/rejects) for audit. |
| `QuantResearch/results/risk` | `system` | Aggregated risk metrics, reports, and event logs. |

### Strategy inventory
| Status | Name | Module | Notes |
| --- | --- | --- | --- |
| Active | `sma_atr` | `QuantResearch/strategies/sma_atr.py` | Core ATR trend strategy (used by default StrategyEngine configs/tests). |
| Active | `regime_sma` | `QuantResearch/strategies/regime_sma.py` | Regime-aware SMA blend; used in multi-strategy YAMLs. |
| Active | `band_mean_revert` | `QuantResearch/strategies/band_mean_revert.py` | ATR band mean-revert with optional shorts. |
| Active | `bollinger_mean_revert` | `QuantResearch/strategies/bollinger_mean_revert.py` | Bollinger-only bias strategy for EURUSD grids. |
| Active | `ma_crossover` | `QuantResearch/strategies/ma_crossover.py` | Simple fast/slow crossover referenced by `tests/test_strategy_registry.py`. |
| Active | `momentum_breakout` | `QuantResearch/strategies/momentum.py` | Breakout momentum block (used in multi-strategy configs/tests). |
| Active | `regime_vol_ml` | `QuantResearch/strategies/regime_vol_ml.py` | Vol regime classifier wrapper (consumes latest pointer). |
| Active | `xgb_signal` | `QuantResearch/strategies/xgb_signal.py` | Production ML signal (default in V2 baseline). |
| Archived | `ma_cross` | `QuantResearch/archive/strategies/ma_cross.py` | Legacy queue-based MA strategy (depended on `core/events`, no longer wired). |
| Archived | `mean_reversion_strategy` | `QuantResearch/archive/strategies/mean_reversion.py` | Standalone pandas helper never used by StrategyEngine. |

### Script inventory
| Status | Script | Location | Notes |
| --- | --- | --- | --- |
| Active | `build_clean_usdjpy_dataset.py` | `QuantResearch/scripts/build_clean_usdjpy_dataset.py` | Generates the v2 clean dataset + feature snapshot. |
| Active | `backtest_strategy.py` | `QuantResearch/scripts/backtest_strategy.py` | Single-run backtest entrypoint referenced in docs. |
| Active | `run_walkforward.py` | `QuantResearch/scripts/run_walkforward.py` | Rolling WF harness for gating models. |
| Active | `run_risk_sim.sh` | `QuantResearch/scripts/run_risk_sim.sh` | Paper/risk replay wrapper used in runbooks/CI. |
| Active | `cleanup_artifacts.py` | `QuantResearch/scripts/cleanup_artifacts.py` | Retention-aware results/stats cleanup tool. |
| Active | `train_xgb_usdjpy.py` / `train_vol_regime_usdjpy.py` | `QuantResearch/scripts/` | Produce the v2 ML artifacts. |
| Archived | `cleanup_outputs.py` | `QuantResearch/archive/scripts/cleanup_outputs.py` | Deprecated raw file cleanup (superseded by `cleanup_artifacts.py`). |
| Archived | `compute_indicators.py` | `QuantResearch/archive/scripts/compute_indicators.py` | Early feature builder superseded by the v2 dataset pipeline. |
| Archived | `analyze_cost_profiles.py` | `QuantResearch/archive/scripts/analyze_cost_profiles.py` | Manual cost-profile aggregation (unused in current configs). |
| Archived | `compare_cost_scenarios.py` | `QuantResearch/archive/scripts/compare_cost_scenarios.py` | Experimental stress tester no longer referenced. |
| Archived | `grid_search_rsi_trailing.py` | `QuantResearch/archive/scripts/grid_search_rsi_trailing.py` | Legacy EURUSD parameter grid harness. |

### Data outputs
- Files under `QuantResearch/data/outputs/stats|trades` that are referenced by retained runs stay on disk. `scripts/cleanup_artifacts.py --prune-data-outputs` automatically figures this out by reading `summary.json.artifacts`.
- Any new run meant to survive cleanup **must** add `"retention": "<label>"` to its `summary.json` so the script knows to keep its stats/trades.

### Archiving workflow
1. Archive a run/model before deletion: `tar -czf QuantResearch/archive/results/<run_id>.tar.gz QuantResearch/results/<run_id>`.
2. Update the tables above with the archive location (`archive/results/...` or `archive/models/...`).
3. Remove the original directory (either manually or via `scripts/cleanup_artifacts.py --apply`).
4. Document the change in the relevant README or PR so reviewers know where to find the evidence.

### Cleanup workflow
```bash
# Preview deletions (omit --apply for dry-run behavior)
python QuantResearch/scripts/cleanup_artifacts.py \
  --results QuantResearch/results \
  --data-root QuantResearch/data/outputs \
  --prune-data-outputs

# Apply deletions + prune dangling stats/trades
python QuantResearch/scripts/cleanup_artifacts.py \
  --results QuantResearch/results \
  --data-root QuantResearch/data/outputs \
  --apply --prune-data-outputs
```

Cleanup defaults keep `retention` values `baseline`, `wf_baseline`, and `archive`. To add custom retention tags, extend the tables above and pass `--keep` accordingly.
