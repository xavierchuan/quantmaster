# FX Backtest & Execution Stack

End-to-end foreign-exchange research and trading toolkit that links feature engineering, backtesting, ML-driven signal generation, and OANDA execution into one repo.

## Highlights

- Unified `StrategyEngine` (see `QuantResearch/core/backtest/strategy_engine.py`) powers historical backtests, walk-forward studies, paper trading, and the live runner so signals behave identically across environments.
- Strategy registry ships with SMA/ATR trend, Bollinger & band mean-revert, breakout momentum, and an XGBoost probability model (`QuantResearch/strategies/*`), allowing multi-strategy voting through YAML configs such as `QuantTrader/config/usdjpy_multi_strategy.yaml`.
- Research workflows enforce data-manifest validation, risk sims, KPI summaries (`results/<run_id>/summary.json`), and promotion of vetted artifacts into `QuantTrader/artifacts/` before they are allowed to reach trading.
- Runtime layer contains async OANDA data/execution handlers, event-driven risk checks, and pluggable multi-strategy allocation for both paper (`scripts/paper_trade.py`) and live trading (`scripts/live_trade.py`).
- Monitoring stack (Pushgateway + Prometheus + Grafana) ships ready-to-import risk dashboards (`monitoring/grafana/*.json`), custom drilldown plugins (logs/traces/profiles/metrics), and Slack/pushgateway hooks for diagnostics automation.

## Repository Layout

- `QuantResearch/` – Research code, datasets, strategy implementations, notebooks/scripts, docs, artifacts, and test suites.
- `QuantTrader/` – Trading runtime with execution/risk/data engines, configs, logging, and artifact promotion targets.
- `monitoring/` – Dockerized observability stack plus Grafana dashboards & plugins for metrics/logs/traces/profiles.
- `shared/` – Cross-cutting helpers (`shared/utils/config.py` loads OANDA/Slack/Pushgateway secrets from `.env`).
- `results/` – Canonical run outputs uploaded with PRs (e.g., walk-forward summaries) for auditing.
- `metrics/` – Lightweight operational CSVs (e.g., execution latencies) that can be pushed to Prometheus.

## Quick Start

1. **Clone & create a virtual environment**

   ```bash
   git clone <your fork url>
   cd FX_Backtest
   python -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r QuantResearch/requirements.txt
   pip install -r QuantTrader/requirements.txt
   ```

   Python 3.10+ is recommended for `pandas`/`xgboost` compatibility.

2. **Configure secrets**

   ```bash
   cp .env.demo .env
   # edit .env with your OANDA practice/live credentials + webhook URLs
   source .env
   ```

   All scripts that touch OANDA import from `shared.utils.config`, so missing env vars fail fast.

3. **Prepare data (raw → clean Parquet)**

   - Drop raw CSVs (e.g., `USDJPY_H1_5y.csv`) under `QuantResearch/data/raw/`。
   - Run the unified ingest脚本，输出标准化 Parquet + 清洗报告 + manifest 条目：

     ```bash
     cd QuantResearch
     python scripts/ingest_fx.py \
       --symbol USDJPY \
       --input data/raw/USDJPY_H1_5y.csv \
       --output data/clean/USDJPY_H1_clean.parquet \
       --report data/clean/reports/USDJPY_H1_clean_report.json
     ```

     输出位于 `data/clean/<symbol>_H1_clean.parquet`，报告包含缺口、补数、异常点统计。

   - 每次 ingest 后运行 manifest 校验（CI 也会执行）：

     ```bash
     python scripts/verify_manifest.py --manifest data/_manifest.json
     ```

   - 旧的按品种清洗脚本（如 `build_clean_usdjpy_dataset.py`）仍可用于实验性 pipeline，但推荐逐步迁移到统一 ingest。

4. **Run a backtest**

   ```bash
   python QuantResearch/scripts/backtest_strategy.py \
     --csv QuantResearch/data/raw/USDJPY_H1.csv \
     --symbol USDJPY \
     --fast 20 --slow 80 \
     --strategies QuantTrader/config/usdjpy_multi_strategy.yaml
   ```

   The script validates the dataset, runs the engine, and writes KPIs plus `equity/`, `trades/`, and `stats/` artifacts under `QuantResearch/data/outputs/`.

5. **Train or refresh the XGBoost signal**

   ```bash
   python QuantResearch/scripts/train_xgb_usdjpy.py \
     --csv QuantResearch/data/raw/USDJPY_H1.csv \
     --symbol USDJPY \
     --out QuantResearch/artifacts/models/usdjpy_h1_xgb
   ```

   This exports `model.json`, feature lists, thresholds, and updates `usdjpy_h1_xgb_latest.json` so trading configs can point to the latest model.

   Backtests now load the ML config from `QuantResearch/config/usdjpy_xgb_backtest.yaml`, while `QuantTrader/config/usdjpy_xgb.yaml` stays reserved for paper/live trading with real sizing.
   > Long-only v2 baseline已冻结：回测 `QuantResearch/results/20251116_181739`、WF `results/walkforward_usdjpy_xgb_v2_final_20251116_181814`。如需实验 short leg / stacking 等新想法，请复制该 YAML（例如 `_short.yaml`）后再调整，避免破坏基线。

6. **Run walk-forward analysis (optional gating)**

   ```bash
   python QuantResearch/scripts/run_walkforward.py \
     --config QuantTrader/config/usdjpy_multi_strategy.yaml \
     --csv QuantResearch/data/raw/USDJPY_H1.csv \
     --train-bars 4000 --test-bars 1000 \
     --output-root QuantResearch/results \
     --label usdjpy_xgb
   ```

   Each window produces metrics and a `summary.json` under `QuantResearch/results/<run_id>/`. Reference these run IDs in PRs.

7. **Promote artifacts to the trader**

   After validating a run, sync configs/params into `QuantTrader/artifacts/` (see `QuantTrader/artifacts/README.md`):

   ```bash
   cp QuantTrader/config/usdjpy_multi_strategy.yaml QuantTrader/artifacts/config/
   cp QuantResearch/artifacts/models/usdjpy_h1_xgb_latest.json QuantTrader/artifacts/params/
   ```

8. **Paper trading or live execution**

   - Paper (uses live pricing -> StrategyEngine -> simulated fills):

     ```bash
     python QuantTrader/scripts/paper_trade.py \
       --config QuantTrader/config/usdjpy_multi_strategy.yaml \
       --symbol USDJPY \
       --timeframe 60s
     ```

   - Live example (direct OANDA handler + RSI strategy template, see `QuantTrader/scripts/live_trade.py`):

     ```bash
     python QuantTrader/scripts/live_trade.py
     ```

   Customize the risk manager, strategy, and execution handler before pointing to a funded account.

9. **Spin up monitoring (optional but recommended)**

   ```bash
   docker compose up -d
   ```

   This launches Pushgateway (`:9091`), Prometheus (`:9090`), and Grafana (`:3000`). Import `monitoring/grafana/risk_metrics_dashboard.json` and enable the bundled drilldown plugins for logs/traces/profiles/metrics exploration.

## Common Workflows

- **Data quality gating:** `python QuantResearch/scripts/watch_quality.py` or the CI-friendly `scripts/watch_risk_metrics.py` push metrics to Slack/Pushgateway before PRs merge.
- **Batch experiments:** `python QuantResearch/scripts/run_batch_backtests.py --config config/eurusd_grid.yaml` sweeps parameter grids and streams metrics under `results/<batch>/`.
- **Stress testing:** `python QuantResearch/scripts/validate_stress_scenarios.py --config ...` replays adverse cost scenarios to validate drawdown budgets.
- **Risk sims:** `RUN=<run_id> ./QuantResearch/scripts/run_risk_sim.sh && ./QuantResearch/bin/backfill_risk.sh` keep `results/risk/metrics.csv` aligned with latest runs.
- **Account snapshots & live equity:** `python QuantTrader/scripts/export_account_snapshot.py --out QuantTrader/results/execution/account_snapshots.csv` + `python QuantTrader/scripts/export_live_equity.py --fills QuantTrader/results/execution/live/fills.csv` record实盘余额/NAV/每日PnL。post_session.sh 已自动串联上述脚本，并在设置 `PUSHGATEWAY_URL` 时管道 `QuantTrader/scripts/export_account_metrics_prom.py | curl .../metrics/job/account_state` 将余额/NAV/保证金指标推送到 Pushgateway 供 Prometheus/Grafana 可视化。

## Monitoring & Diagnostics

- `QuantResearch/scripts/export_metrics_prom.py` streams aggregated KPIs to Pushgateway (`PUSHGATEWAY_URL`).
- `QuantResearch/scripts/notify_risk_metrics.sh` wraps `watch_risk_metrics.py` to send Slack alerts using `SLACK_RISK_WEBHOOK`.
- Grafana plugins under `monitoring/grafana/plugins/grafana-*-app/` document the queryless drilldown experiences for logs (Loki), metrics (Prometheus), traces (Tempo), and profiles (Pyroscope).
- `monitoring/grafana/risk_metrics_dashboard.json` 只聚焦策略/执行风险（rejects、latency、exposure、PnL等），供交易/风控/工程团队使用。
- `monitoring/grafana/account_state_dashboard.json` 专门展示账户资金（Balance、NAV、Margin、Unrealized PnL、Margin Ratio 警报）。导入该 Dashboard 后即可看到 `Account balance floor` 与 `Margin ratio low` 告警，适合 Ops/Treasury 监控账户安全。

### Portfolio Run Log

- `fx_top5_baseline_20251123_114839` – 六品种 baseline 去除 USDJPY 权重，组合指标：Sharpe 3.98、MaxDD 1.37%。已将归一化权重同步到 `QuantTrader/config/fx_top6_weights.json` 并复制到 `QuantTrader/artifacts/config/`，当前权重：AUDUSD 0.3424、EURUSD 0.2410、USDCHF 0.4166（GBPUSD/GBPJPY/USDJPY=0）。

## Risk Scaling

- `QuantTrader/config/risk_profile.yaml` 定义执行层的 `risk_scale`/`max_leverage`。研究层回测保持 1× baseline，实盘通过该文件统一调仓。调参后复制到 `QuantTrader/artifacts/config/` 以便审计。
- `QuantTrader/scripts/paper_trade.py` 与 `live_trade.py` 在启动时读取风险配置（可用 `--risk-profile` 覆盖，默认指向上面的 YAML），并将策略 `qty` 乘以 `risk_scale`。例如将 `risk_scale` 调至 `0.6/1.2` 即可全局降/提仓。
- `QuantTrader/scripts/export_account_metrics_prom.py --risk-profile ...` 会把 `fx_risk_scale`/`fx_account_max_leverage` 及实时 `fx_account_leverage`（通过 OANDA marginUsed + marginRate 推算）推送到 Pushgateway，Grafana Account Dashboard 因此能显示当前杠杆与设定值。
- `monitoring/grafana/leverage_dashboard.json` 新增专用杠杆面板（当前 Leverage、限制、风险倍数曲线）。导入后即可在 Grafana 中对比 `fx_account_leverage` 与 `fx_account_max_leverage` 并设置相关告警。
- 2025-11-23：实盘 risk_scale 调整为 1.5×（见 `QuantTrader/config/risk_profile.yaml`，已同步到 `QuantTrader/artifacts/config/`）。

## Testing & Validation

- Unit tests: `pytest QuantResearch/tests QuantTrader/tests`.
- Strategy registry coverage: `QuantResearch/tests/test_strategy_registry.py` ensures new strategies register correctly; add fixtures before contributing.
- Result validation: `python QuantResearch/scripts/validate_results.py QuantResearch/results/<run_id>` checks KPI completeness + data references.
- Data feed/execution smoke tests: `python QuantTrader/tests/test_execution_adapters.py` mocks OANDA flows.

## Extending the Stack

1. Implement a new research strategy under `QuantResearch/strategies/` and decorate it with `@register("my_strategy")`.
2. Reference it inside a config YAML (e.g., `usdjpy_multi_strategy.yaml`) with weights/params.
3. Add risk rules in `QuantTrader/core/risk/` if the position sizing model needs to change.
4. Document any new process in `QuantResearch/docs/` or module-level READMEs so CI reviewers have breadcrumbs.

## Related Docs

- `QuantResearch/README.md` – data submission rules, risk/diagnostics workflow.
- `QuantTrader/artifacts/README.md` – promotion checklist for configs/params.
- `monitoring/grafana/plugins/*/README.md` – upstream plugin instructions.

## License

No open-source license is declared yet. Keep the repository private or add a LICENSE file before publishing.
