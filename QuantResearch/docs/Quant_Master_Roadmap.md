# Quant Master Roadmap (12-Month Edition)

Status tags: done / in_progress / pending. Add PR/run ids in Notes when applicable.

## Phase 0 — Current State Assessment
You already have a functioning FX quant research & trading pipeline. This phase documents your current progress and highlights the missing components to reach institutional grade.

### You Already Have
- [Status: done] Complete FX backtesting framework
- [Status: done] Paper & live trading execution layer
- [Status: done] OANDA integration with real-time metrics streaming
- [Status: done] Prometheus + Pushgateway + Grafana monitoring
- [Status: done] Multi-asset walkforward validation
- [Status: done] Stress testing framework
- [Status: done] Portfolio optimization
- [Status: done] Risk scaling (risk_scale + max_leverage)
- [Status: done] Automated NAV / margin monitoring
- [Status: done] Hourly bar strategy pipeline

### Missing Components (next 12 months)
- [Status: pending] Multi-model layer (MML)
- [Status: pending] Multi-timeframe architecture
- [Status: pending] Multi-asset (FX + stocks + ETFs + crypto)
- [Status: pending] Factor research (alphalens-style)
- [Status: pending] Execution alpha & cost models
- [Status: pending] MLOps for nightly model refresh
- [Status: pending] Unified data pipeline
- [Status: pending] Unified risk engine

---

## Phase 1 — FX System Stabilization (0–3 Months)

### 1.1 Build a Fully Automated FX Data Pipeline
- [Status: in_progress] ingest_oanda.py (nightly)
- [Status: in_progress] validate_dataset.py (nightly)
- [Status: in_progress] build_manifest.py (nightly)
- [Status: pending] Create data quality dashboards
- [Status: pending] Versioned data (manifest with hash, start/end, fields)

**Goal:** Never manually download CSV again.

### 1.2 Move to Single-Stream Multi-Pair OANDA Feed
- [Status: in_progress] Subscribe all FX pairs through one OandaPricingStream
- [Status: in_progress] One queue → route to aggregators by symbol
- [Status: in_progress] Multiple engines in one process
- [Status: in_progress] Unified heartbeat, latency tracking, kill-switch

**Result:** Your execution layer becomes institutional-grade.

### 1.3 Robust Risk Scaling Framework
- [Status: done] risk_profile.yaml (risk_scale, max_leverage, floors)
- [Status: in_progress] Paper/live runners read it dynamically
- [Status: done] risk_scale exported to Prometheus
- [Status: pending] Auto risk adjustments based on DD/margin events

### 1.4 Observability Foundation
- [Status: in_progress] Baseline Prom/Grafana coverage (heartbeat, per-symbol exposure, PnL, latency, basic slippage)
- [Status: pending] Strategy health dashboard (heartbeat/exposure/PnL/latency/slippage)
- [Status: pending] Alert rules aligned with kill-switch

---

## Phase 2 — Expand Beyond FX (3–6 Months)

### 2.1 Build Equities Data Pipeline
- [Status: pending] ingest_yfinance.py or Polygon
- [Status: pending] Validate dataset
- [Status: pending] Daily Bar (D1) / Hourly (H1)
- [Status: pending] Feature set:
  - SMA/EMA
  - ATR
  - Volatility
  - Volume
  - MACD
  - Momentum indicators

### 2.2 Build First Stock Strategy (Low Frequency)
- [Status: pending] H1 or H4 models
- [Status: pending] Apply your FX ATR SL/TP logic
- [Status: pending] Add ML models (XGB, LGBM) as filters
- [Status: pending] Only long positions initially

### 2.3 Unified Cross-Asset Portfolio
- [Status: pending] FX strategies (3–5)
- [Status: pending] US large-cap stocks (10–20)
- [Status: pending] ETFs (3–5)

**Result:** Much lower drawdowns, much smoother equity curve.

---

## Phase 3 — Multi-Model Layer (6–9 Months)

A serious hedge fund (Citadel / Two Sigma) always uses MML.

### 3.1 Add Multiple Models per Instrument
- [Status: pending] XGB directional model
- [Status: pending] LightGBM directional model
- [Status: pending] Logistic classifier
- [Status: pending] Regime classifier
- [Status: pending] Volatility model
- [Status: pending] Rule-based alpha
- [Status: pending] Noise filters

### 3.2 Meta Model Fusion
Combine signals:

```
meta_signal = w1*xgb + w2*lgbm + w3*regime + w4*trend + w5*vol_filter
```

### 3.3 Automatic Nightly Training Pipeline
- [Status: pending] Pull data
- [Status: pending] Validate data
- [Status: pending] Feature generation
- [Status: pending] Model re-training
- [Status: pending] Backtest
- [Status: pending] Walkforward
- [Status: pending] Stress test
- [Status: pending] Promotion rules:
  - If new model > old model: deploy
  - Else: keep old

### 3.4 Shadow-to-Live Promotion & Freeze
- [Status: pending] Shadow run (no trade) with logs in `results/shadow_models`
- [Status: pending] Promotion rules (latency/accuracy/leakage checks) → low-weight live → full weight
- [Status: pending] Infra freeze checkpoint after Phase 3 (stability window)

This is exactly what Citadel does.

---

## Phase 4 — Institutional Execution & Risk (9–12 Months)

### 4.1 Execution Alpha
- [Status: pending] Smart order routing (SOR)
- [Status: pending] VWAP/TWAP execution
- [Status: pending] Slippage predictor (XGB)
- [Status: pending] Spread predictor
- [Status: pending] Dynamic fee / cost modeling

### 4.2 Unified Global Portfolio
- [Status: pending] FX (10+)
- [Status: pending] US Stocks (50+)
- [Status: pending] ETFs (20+)
- [Status: pending] Crypto (10+)

Run portfolio optimizer (Markowitz + CVaR).

### 4.3 Unified Risk Engine
- [Status: pending] VaR / ES
- [Status: pending] Real-time exposure tracking
- [Status: pending] Kill-switch based on:
  - Latency spikes
  - NAV floor
  - Large slippage
  - High leverage
- [Status: pending] Auto risk scaling
- [Status: pending] Auto hedging logic (optional)
- [Status: pending] Cross-asset beta / correlation / PCA risk monitoring

### 4.4 Institutional Monitoring Platform
- [Status: pending] All assets in Grafana:
  - Real-time equity curves
  - PnL attribution
  - Fill quality
  - Slippage
  - Latency
  - Margin / NAV
  - Risk Scale
  - Model drift
  - Data quality

---

## Final Result (12 Months)

You will have:
- Multi-asset research platform
- Multi-model architecture
- Unified global portfolio
- Automatic model updates
- Institutional-grade execution
- Institutional-grade monitoring
- Sharpe 3–6 target
- MaxDD < 3–6%
- System scalable to >10M USD AUM

This roadmap turns you into a **one-person Citadel research pod**.

---

## Appendix: Folder Structure
(Condensed for clarity)

```
Quant/
├── QuantResearch/
│   ├── config/
│   ├── core/
│   ├── scripts/
│   ├── data/
│   ├── artifacts/
│   ├── results/
│   ├── docs/
│   └── tests/
├── QuantTrader/
│   ├── config/
│   ├── core/
│   ├── scripts/
│   ├── results/
│   ├── logs/
│   └── tests/
├── monitoring/
├── README.md
└── roadmap.md
```

---

## You Now Have a 12-Month, Full Institutional-Grade Plan
This roadmap is executable, realistic, and exactly what hedge funds do.
