# FX_Backtest Roadmap (Citadel-Style Execution Stack)

## Legend
- ✅ Done / stable
- 🟡 In progress / short-term (<2 weeks)
- 🔴 Planned / mid-term (2–6 weeks)
- 📌 Owner: default `QuantResearch` unless noted

---
## Phase 0 (Now)
- ✅ Freeze baseline combo `fx_top5_baseline_20251123_114839` in `README.md` + artifacts
- ✅ Weight + risk profile synced to `QuantTrader/artifacts/config`

---
## Phase 1 (Week 1–2) – Data & Kill Switch (Highest Priority)
1. 🟡 **Data Hardening**
   - Automated ingest → clean Parquet (`QuantResearch/scripts/ingest_fx.py`)
   - Time alignment, calendar pruning, NA/dup checks, z-score outlier report
   - Manifest/hash diff CI gate (PR fails if manifest not updated)
   - Deliverables: `data/clean/*.parquet`, `data/_manifest.json` versioned, nightly ingest job
2. 🟡 **Kill Switch + Recovery**
   - Module `QuantTrader/utils/kill_switch.py` with margin_ratio/NAV floor logic
   - Inject into `paper_trade.py`/`live_trade.py`; trigger flattens all + block new orders
   - Metrics: `fx_kill_switch_tripped`, `fx_kill_switch_reason`; Grafana alert
   - Warm-up & auto-reconcile (fetch remote positions, fill ledger)

---
## Phase 2 (Week 2–3) – Execution TCA & Replay
1. 🔴 **Execution TCA + Slippage**
   - `QuantTrader/execution/tca_logger.py` logging submit vs fill price, latency, 5m/30m markout
   - Post session aggregates → `results/execution/tca_summary.json` + detailed CSV
   - Prom metrics: `fx_slippage_bps`, `fx_fill_latency_ms_p95`, `fx_markout_5m`
2. 🔴 **Trade Replay & Warm Starts**
   - Enhance runner to replay ledger + warm-up N bars after restart
   - Crash recovery doc/runbook

---
## Phase 3 (Week 3–4) – Feature Store & Multi-Model
1. 🔴 **Feature Engineering Upgrade**
   - Multi-frequency features (M1/M5/H1 alignment)
   - Feature importance + leakage checks logged per training run
   - Feature store: `feature_store/{symbol}/{freq}.parquet`
2. 🔴 **Multi-Model Layer (MML)**
   - Train additional models: LightGBM, RandomForest, Logistic baseline
   - Ensemble strategy `strategies/ml_ensemble.py` with configurable weights
   - Shadow deployment: run models live (no trade) for comparison
   - Model registry: `artifacts/models/<symbol>/<timestamp>/` + `latest.json`

---
## Phase 4 (Week 4–6) – Risk & Stress Expansion
1. 🔴 **Execution Stress Tests**
   - Monte Carlo / Bootstrap walkforward resampling
   - Execution delay / rejection simulation knobs in backtest
2. 🔴 **Portfolio Risk Enhancements**
   - Per-position exposure metrics (`fx_exposure_{symbol}`)
   - Online VaR / CVaR (EWMA/HS) Prom metrics, Grafana panel
   - Overnight / session-based risk regimes (risk_scale schedule)
3. 🔴 **Exposure Heatmap & Alerts**
   - Grafana heatmap for symbol exposure vs limits
   - Alerts for VaR breach, overnight max size, etc.

---
## Continuous Tasks
- CI/CD improvements (lint + small backtest per PR, nightly ingest & monitoring self-test)
- Secrets management hardening (`.env` → secrets store)
- Documentation updates for new modules (runbooks, ingestion spec, risk ops manual)

---
## Quick Wins (48h backlog)
- Kill switch MVP + Prom metrics + Grafana alert
- TCA logging scaffold (submit/fill diff)
- Parquet export for existing clean CSV + cleaning report
- Cron/post-session auto push `fx_account_leverage` / `fx_risk_scale`

