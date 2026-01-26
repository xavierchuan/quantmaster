# Quant_Master

**End-to-end FX quantitative trading system: from research to live execution.**

## Performance

| Metric | Value |
|--------|-------|
| **Sharpe Ratio** | 3.98 |
| **Max Drawdown** | 1.37% |
| Portfolio | AUDUSD, EURUSD, USDCHF (optimized weights) |

*Walk-forward validated on 5 years of hourly data.*

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Quant_Master                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  QuantResearch (Research Layer)     QuantTrader (Execution)     │
│  ┌─────────────────────────┐       ┌─────────────────────────┐  │
│  │ Data Pipeline           │       │ OANDA v20 Adapter       │  │
│  │   • Ingest & QA         │       │   • Async streaming     │  │
│  │   • Manifest validation │       │   • Order execution     │  │
│  │                         │       │                         │  │
│  │ Strategy Engine ────────┼──────▶│ Risk Engine             │  │
│  │   • Unified backtest/   │       │   • Position limits     │  │
│  │     paper/live runner   │       │   • Exposure checks     │  │
│  │                         │       │                         │  │
│  │ ML Models (62 trained)  │       │ Kill Switch             │  │
│  │   • XGBoost signals     │       │   • Account-level halt  │  │
│  │   • Regime detection    │       │   • Drawdown triggers   │  │
│  │                         │       │                         │  │
│  │ Walk-Forward Framework  │       │ Paper / Live Trading    │  │
│  └─────────────────────────┘       └───────────┬─────────────┘  │
│                                                │                │
│                    ┌───────────────────────────▼──────────────┐ │
│                    │         Monitoring Stack                 │ │
│                    │  Prometheus + Grafana (5 Dashboards)     │ │
│                    │  • Risk metrics    • Account state       │ │
│                    │  • Leverage        • Execution latency   │ │
│                    └──────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Technical Decisions

### 1. Unified Strategy Engine
**Same code for backtest, paper, and live trading** — eliminates backtest-to-production discrepancies.

```
StrategyEngine → [Backtest Mode] → Historical simulation
             → [Paper Mode]    → Live prices, simulated fills
             → [Live Mode]     → Real execution via OANDA
```

### 2. ML-Driven Regime Awareness
XGBoost probability signals combined with **volatility/trend regime classification**:
- High volatility → tighter stops, reduced position size
- Trending regime → allow profits to run
- Range-bound → mean-reversion bias

### 3. Multi-Layer Risk Management
```
Order Level (RiskEngine)          Account Level (KillSwitch)
├─ Position size limits           ├─ Max drawdown trigger
├─ Exposure per instrument        ├─ Margin ratio alerts
├─ Correlation checks             └─ Emergency position flatten
└─ Slippage validation
```

### 4. Crash Recovery via Event Ledger
All state changes logged to append-only ledger → **full state reconstruction** after crashes.
- No lost positions
- Audit trail for every trade decision

### 5. Production-Grade Observability
Docker Compose stack with:
- **Prometheus** — metrics collection
- **Grafana** — 5 pre-built dashboards (risk, account, leverage, execution, PnL)
- **Pushgateway** — batch metric ingestion
- **Slack alerts** — real-time notifications

---

## Tech Stack

| Category | Technologies |
|----------|-------------|
| Core | Python 3.10+, asyncio, pandas |
| ML | XGBoost, scikit-learn |
| Execution | OANDA v20 REST API |
| Monitoring | Prometheus, Grafana, Pushgateway |
| Infrastructure | Docker Compose |

---

## Codebase Statistics

| Metric | Count |
|--------|-------|
| Core Python code | ~8,000 lines |
| Strategy implementations | 9 |
| Trained ML models | 62 |
| Grafana dashboards | 5 |
| Unit test coverage | Full strategy registry |

---

## Quick Start

```bash
# 1. Setup
git clone <repo> && cd Quant_Master
python -m venv .venv && source .venv/bin/activate
pip install -r QuantResearch/requirements.txt -r QuantTrader/requirements.txt

# 2. Configure OANDA credentials
cp .env.demo .env && vim .env

# 3. Run backtest
python QuantResearch/scripts/backtest_strategy.py \
  --config QuantResearch/config/usdjpy_xgb_backtest.yaml

# 4. Paper trading (live prices, simulated execution)
python QuantTrader/scripts/paper_trade.py \
  --config QuantTrader/config/usdjpy_multi_strategy.yaml

# 5. Launch monitoring stack
docker compose up -d  # Grafana at :3000
```

---

## Repository Structure

```
Quant_Master/
├── QuantResearch/          # Research layer
│   ├── core/               # Strategy engine, backtest framework
│   ├── strategies/         # 9 strategy implementations
│   ├── scripts/            # Data pipeline, training, walk-forward
│   └── artifacts/models/   # 62 trained XGBoost models
├── QuantTrader/            # Execution layer
│   ├── core/               # OANDA adapter, risk engine, kill switch
│   ├── scripts/            # Paper & live trading runners
│   └── artifacts/          # Promoted configs for production
├── monitoring/             # Observability stack
│   ├── docker-compose.yml
│   └── grafana/            # 5 dashboard JSONs + plugins
└── shared/                 # Cross-cutting utilities
```

---

## Design Trade-offs

| Decision | Trade-off | Rationale |
|----------|-----------|-----------|
| Python over C++ | Latency vs. development speed | FX hourly signals don't need sub-ms latency |
| Unified engine | Complexity vs. consistency | One codebase = no backtest-live divergence |
| Event ledger | Storage vs. recovery | Disk is cheap; lost positions are expensive |
| OANDA API | Feature limits vs. reliability | Stable retail API, good for initial deployment |

---

## Contact

Built by Xiaochuan Li — [GitHub](https://github.com/xavierchuan) | [LinkedIn](https://linkedin.com/in/xiaochuan-li)
