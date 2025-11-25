# Live Trading Roadmap for FX_Backtest

This document expands the previous high-level plan into actionable workstreams and establishes objective criteria for calling the project “live-trading ready” at a personal-fund level (with room to scale toward institutional standards).

## Vision & Success Criteria

### Strategy Performance Targets
- Annualized return ≥ **18%** over the most recent 3-year walk-forward or regime-simulated window.
- Net Sharpe ratio (daily returns, annualized) ≥ **1.4**; Sortino ≥ **2.0**.
- Maximum peak-to-trough drawdown ≤ **10%** with recovery time ≤ **60 trading days**.
- Hit rate ≥ **45%** *or* payoff ratio (avg win / avg loss) ≥ **1.6**, verified in both backtest and paper trading.
- Turnover- and fee-adjusted edge persists after adding 2× actual commission + 1.5× historical slippage (“haircut rule”).

### Risk & Capital Protection
- Intraday VaR (99%, 1-day) < **8% of allocated capital**; stressed VaR < **12%**.
- No more than **1** unmitigated limit breach per quarter; all breaches auto-dampen exposure within 1 minute.
- Gross leverage capped at **3×** with soft alerts at 2.5×.

### Reliability & Operations
- End-to-end pipeline (data → research → backtest → paper → live) produces identical signals within ±0.5 bps drift.
- Trading stack uptime ≥ **99.5%** during trading hours; recovery procedures executable within 5 minutes (documented runbook).
- Monitoring coverage: strategy PnL, exposure, order rejects, latencies, and heartbeat metrics, each with alert TTA < 2 minutes.
- All strategy/config changes logged with author, timestamp, diff, review note; rollback script tested quarterly.

### Compliance & Security (personal-fund scope)
- Secrets stored in encrypted vault (.env access limited), rotation cadence ≤ 90 days.
- Dual confirmation for deploying new models/parameters to `QuantTrader`.
- Daily backups of configs (`QuantResearch/config`), data snapshots, and equity curves retained ≥ 1 year.

## Phase 0 – Current-State Assessment (Week 0–1)
- **Goals**: Inventory capabilities, align metrics, and flag critical gaps.
- **Key Tasks**
  - Map data flow from `data/` ingestion scripts to `strategy_engine.py` inputs; document latency, retention, missing instruments.
  - Review existing configs (`config/*.yaml`) to catalog parameter surfaces and dependencies.
  - Audit `scripts/backtest_strategy.py` vs. `core/backtest/strategy_engine.py` usage to identify duplicated logic.
  - Define acceptance metrics (above) in `metrics/perf.py` outputs; ensure current reporting covers them.
- **Deliverables**
  - Current-state matrix (“have / missing / partial”) with owners.
  - Gap list prioritized by severity (blocking live, performance risk, nice-to-have).
- **Exit Criteria**
  - Stakeholders agree on scope, timeline, and quantitative KPIs.
  - No unknown components between market data and execution pathway.

## Phase 1 – Data & Research Stack (Week 1–3)
- **Goals**: Deterministic, validated data feeding identical research/backtest/live views.
- **Current Issues to Resolve**
  1. `scripts/get_candles.py` 需要人工触发，不会记录抓取窗口、校验状态或失败重试，导致 `data/raw/*` 中数据更新时间和覆盖区间无法追溯。
  2. `data/raw/`、`data/derived/` 缺少 manifest/元数据文件，团队无法得知每个 CSV 的行数、时间范围、字段或哈希，也无法在回测前确认数据是否被篡改。
  3. `scripts/backtest_strategy.py` 仅依赖传入 CSV，未调用任何数据质量检查：如果存在缺口、重复时间戳或异常值，会直接进入策略逻辑，导致回测结果不可信。
  4. 当前 `results/<run_id>/` 只保存 equity/trades/stats，没有附带“此次回测的原始数据报告（来源、版本、缺口百分比）”，难以追踪问题或复现实验。
  5. 缺乏单独的数据流程文档：新人无法快速理解从 OANDA 抓取 → 存储 → 验证 → 回测的全链路，也没有出现故障时的回滚/补数步骤。
- **Key Tasks**
  - Build ETL scripts in `scripts/` (or `QuantTrader/data`) with schema validation, checksum, and metadata (source, timezone, quality).
  - Add data quality dashboard: gap detection, outlier reports (`charts/` + notebook or CLI).
  - Version configs and datasets: tag releases in git and store manifest files (e.g., `data/_manifest.json`) with hashes + date.
  - Introduce replay utilities to regenerate `pandas` frames exactly as used in backtest for investigations.
- **Deliverables**
  - Automated nightly data ingest with logs in `metrics/`.
  - Data-quality scorecard appended to each backtest run (stored alongside `results/`).
  - Playbook `docs/data_pipeline.md` describing fetch/validation/rollback procedures.
  - CI guard (`.github/workflows/data-integrity.yml`) that runs `scripts/check_data_integrity.py` to block failing validation or unreviewed hash drift.
  - Testing coverage per `docs/testing_plan.md`（ingest dry-run、manifest diff、DQ CLI、CI 守门）。
- **Exit Criteria**
  - Backtest vs. raw data mismatch < 0.1% rows; no orphan columns or timezone drift.
  - Regression test proves deterministic reproduction (hash match) of a reference dataset.
  - Automated ingest pipeline (`scripts/ingest_oanda.py` + schedule + cron) running daily with green CI data-integrity checks.
  - Phase 1 测试套件（ingest dry-run、manifest diff、DQ CLI、CI 守门）全部通过。

## Phase 2 – Backtest & Simulation Enhancements (Week 3–6)
- **Goals**: Robustness against regime changes with comprehensive metrics.
- **Key Tasks**
  - Extend `strategy_engine.py` to support flexible cost/slippage hooks, multi-instrument hedging, and overlapping positions.
  - Expose weighted multi-strategy orchestration（`strategies[].weight` + `strategy_mode`）、可配置成本/滑点 profile（YAML/JSON），以及 stress hooks（cost/vol 扩张、随机 skip trades）便于稳健性测试。
  - Implement Monte Carlo path reshuffling & bootstrapped drawdown scripts in `scripts/`.
  - Enable batch backtests driven by config grids (`config/grid/*`); export metrics to `results/summary.csv` + charts.
  - Add statistical tests: probability of ruin, parameter sensitivity (heatmaps), walk-forward analytics.
- **Deliverables**
  - CLI entry point (`python scripts/backtest_strategy.py --config ... --grid ...`) that outputs standardized JSON/CSV metrics.
  - Monte Carlo stress suite（`python scripts/run_monte_carlo.py --run <id> --iterations … --scenario base`）写入 `results/<run>/stress/mc_summary.json` 与 `mc_iterations.csv`，归档 Sharpe/Sortino/Calmar 分位、`p_ruin`，并记录 scenario/seed 便于追溯（由 `python -m unittest tests.test_run_monte_carlo` 覆盖）。
  - Walk-forward CLI（`python scripts/run_walkforward.py --config ... --train-bars ... --test-bars ...`）输出 `walkforward/metrics.csv` + `summary.json`，记录每个窗口 KPI、数据哈希、参数指纹与 pass/fail 标签。
  - 统一压力场景目录（`config/stress_scenarios.yaml`）与 `--scenario/--scenario-file` 接口，确保 `run_batch_backtests.py`、`run_monte_carlo.py` 等输出的每一行 KPI 都包含 scenario 标签与实际成本/滑点/skip 参数，便于审计追溯。
  - Diagnostics CLI（`python scripts/plot_backtest_diagnostics.py --batch-csv ... --walkforward-csv ... --mc-* ... --out charts/...`）生成 heatmap / Monte Carlo box / walk-forward timeline，并写入 `diagnostics_metadata.json` 供报告与复盘。
  - Streamlit 仪表板（`streamlit run apps/diagnostics_dashboard.py`）读取 `charts/diagnostics_*` 中的 metadata/data JSON，供审阅者筛选 run/scenario 并查看 PNG/指标。
  - Visualization templates (equity curve, underwater plot, rolling Sharpe) for every run.
  - 测试矩阵涵盖 KPI 扩展、多策略/成本组合、stress hooks、批量/Monte Carlo（见 `docs/testing_plan.md`）。
- **Exit Criteria**
  - Each strategy variant demonstrates KPIs above: return, Sharpe, drawdown thresholds.
  - Stress tests (±50% volatility, widened spreads) degrade Sharpe by ≤ 30%; remains > 1.0.

## Phase 3 – Execution & Risk Layer (Week 5–8)
- **Goals**: Reliable order routing with embedded risk controls.
- **Key Tasks**
  - Implement execution adapters in `QuantTrader/` with retry, throttling, idempotent order IDs, and state persistence.
  - Introduce risk engine module enforcing per-strategy/account exposure, net leverage, loss caps, and kill-switch triggers.
  - Build paper-trading harness mirroring broker API that logs fills, latency, and deviations vs. expected fills.
  - Wire real-time FX conversion utils (already in `strategy_engine.py`) into execution PnL calculations.
- **Deliverables**
  - Paper/live trading configs separate from backtest configs but sharing parameter schema.
- Risk limit definition file (YAML/JSON) and enforcement logs saved to `results/risk/`.
- Execution/Risk blueprint documented in `docs/execution_risk_plan.md`, covering adapter APIs, risk guards, and test harness expectations.
- Simulation harness（`scripts/simulate_execution.py` + `scripts/run_risk_sim.sh`）可直接读取 `results/<run_id>/summary.json.artifacts.trades`，重放真实回测的 `data/outputs/trades/*.csv` 并生成 `results/execution/<run_id>/`。
- GitHub Action `.github/workflows/risk-sim.yml` 自动定位最新 `results/<run_id>/`、运行 `./scripts/run_risk_sim.sh`、并上传 `results/execution/<run_id>/`、`results/risk/report.csv`、`results/risk/events.jsonl` 供审阅；若 `check_risk_report.py` 发现拒单/kill-switch 超限即阻断 PR。
- Paper risk runbook：`docs/runbook_paper_risk.md` 记录了如何挑选 run、运行 `run_risk_sim.sh`、审阅 `results/risk/*.csv`、以及签署 checklist 的标准流程。
- 限额分离：`QuantTrader/config/risk_limits.yaml`（实盘）与 `risk_limits_sim.yaml`（仿真守门）分别维护，脚本默认使用 sim 限额；runbook 要求在切换环境前注明。
- 风险台账自动化：`bin/backfill_risk.sh` 和 `scripts/backfill_risk_metrics.py` 用于保持 `results/risk/metrics.csv` 与 `results/` 目录同步，CI/本地都可调用。
- Ops runbook (`docs/runbook_ops.md`) 记录 Slack 频道、cron、Prometheus/Grafana 接入及报警 SOP。
- Risk event log/report：拒单与 kill-switch 统一写入 `results/risk/events.jsonl`，通过 `python scripts/risk_report.py --log results/risk/events.jsonl` 输出汇总 CSV 供复盘与监控。
- `scripts/run_risk_sim.sh` + `scripts/check_risk_report.py` 在 CI/Nightly 中执行，若 risk report 超阈值则阻断 PR/部署。
- **Exit Criteria**
  - 30-day paper trading run meeting performance targets within ±15% of backtest metrics.
  - Zero unhandled order rejects; average order ACK latency < 500 ms.
  - Risk engine proves it can flatten positions within 60 seconds during drill.

## Phase 4 – Monitoring, Ops & Security (Week 6–9)
- **Goals**: Visibility and operational readiness.
- **Key Tasks**
  - Instrument components with metrics/logging (e.g., Prometheus exporters or lightweight JSON logs) stored in `metrics/`.
  - Set up dashboards (Grafana or lightweight web UI) covering latency, PnL, exposure, error rates.
  - 将风险台账接入监控：每日运行 `scripts/watch_risk_metrics.py`（或 cron）检查最新 `results/risk/metrics.csv`；在 Streamlit 仪表板新增 risk tab（复用 `scripts/plot_risk_metrics.py`）。
  - Implement alert routing (email/IM/API) with severity levels and acknowledgement tracking.
  - Document runbooks: restart procedures, manual failover, data backfills, emergency flat.
  - Harden secrets: move API keys to encrypted store, rotate, audit access.
- **Deliverables**
  - `docs/runbook.md` with step-by-step instructions and contact tree.
  - 风险指标监控脚本 + 告警配置（rejects>0 或 status=fail 时触发），并在 dashboard/Step Summary 中可视化（风险 tab）。
  - Alert test reports showing notification receipt within SLA.
  - ✅ Prometheus/Grafana pipeline上线：Cron + diagnostics workflow 推送 `risk_*` 指标至 Pushgateway（见 `docs/runbook_ops.md` 2025-11-11 更新）。
  - ✅ 2025-11-11 synthetic risk drill（`run_id=synthetic_fail`）触发 Slack + Grafana 告警并按 runbook 恢复，证实 KPI 守门覆盖 rejects/latency/PnL/exposure/drawdown。
- **Exit Criteria**
  - Monitoring exercises detect synthetic faults in < 2 minutes and on-call executes runbook successfully.
  - Security review checklist signed off (secrets inventory, access control, backup verification).

## Phase 5 – Go-Live & Scaling (Week 9–12)
- **Goals**: Transition from paper to live with controlled capital ramp.
- **Key Tasks**
  - Run “parallel fence” (paper + live tiny capital) for at least 4 weeks; compare fills, slippage, risk events daily（见 `docs/parallel_fence.md` 操作步骤）。
  - Define capital ramp schedule tied to metrics (e.g., +50% capital after Sharpe ≥ 1.4 and drawdown < 5% for 6 weeks).
  - Establish post-trade TCA workflow to recalibrate execution assumptions.
  - Schedule quarterly strategy reviews and incident post-mortems.
- **Deliverables**
  - Launch checklist (pre-flight, during session, post-close) stored in `docs/`.
  - `docs/runbook_go_live.md` capturing pre-flight/parallel fence/T+0流程与 ramp gate 条件。
  - ✅ compare_fills + metrics 自动化：`QuantTrader/bin/post_session.sh` 调用 `scripts/compare_fills.py` + `scripts/update_metrics_from_tca.py`，并推送 Prom/Grafana。
  - Live performance dashboard split by strategy and venue.
- **Exit Criteria**
  - Two consecutive ramp gates passed without KPI breaches.
  - All incidents within the period resolved with documented root cause and action items.

## Phase 0 Gap Matrix (Current Assessment)
| Area | Existing Assets | Status | Gaps / Required Actions | Priority | Owner |
| --- | --- | --- | --- | --- | --- |
| Data ingestion & storage | `scripts/get_candles.py`, `data/raw/*`, `data/csv_feed.py` | Partial | Manual pulls, no automated schedule, no schema/version manifest, single-source (OANDA) & limited validation. Need ETL orchestrator with checksum + metadata, multi-source reconciliation, and retention policy. | P0 | Data/Infra |
| Research & backtest | `scripts/backtest_strategy.py`, `core/backtest/strategy_engine.py`, configs under `config/`, `metrics/perf.py` | Partial | Engine shared but lacks pluggable slippage/cost hooks, Monte Carlo stress, batch grid orchestration, and standardized result schema. | P0 | Research |
| Strategy/config management | YAML configs in `config/`, git history | Partial | No manifest of active configs, no approval workflow, no parameter diff report; ad-hoc naming. Need registry + change log + dependency map. | P1 | Research |
| Execution & paper trading | `QuantTrader/core/execution*.py`, `QuantTrader/core/portfolio.py`, logs | Early | Components exist but not wired to current research configs; no automated reconciliation vs. broker, retry/ACK metrics, or state persistence tested. Need adapter harness + simulation equivalence tests. | P0 | Trading |
| Risk management | `QuantTrader/core/risk/base.py` scaffold | Missing | No quantitative limits (exposure, VaR, drawdown), no kill-switch or enforcement logs. Need dedicated risk config, calculators, and integration in order flow. | P0 | Trading/Risk |
| Monitoring & ops | loguru console logs, ad-hoc scripts | Missing | No metrics pipeline, dashboards, alerting, or runbooks; no backup verification. Need observability stack + SOPs + drill schedule. | P0 | DevOps |
| Compliance & security | `shared/utils/config.py` for secrets | Missing | Secrets stored in plaintext, no rotation, no audit trail. Need vaulting, least-privilege access, change approvals, and backup audits. | P1 | Ops |

## Phase 1 Initial Backlog (Data Validation & Manifest)
| Task | Description | Owner | Dependencies | Definition of Done |
| --- | --- | --- | --- | --- |
| Dataset manifest generator | Build `scripts/build_dataset_manifest.py` to scan `data/raw` & `data/derived`, compute row counts, column schema, time span, checksum, and emit `data/_manifest.json` per dataset version. | Data/Infra | Access to storage, hashing lib (`hashlib`) | Manifest auto-updates via CI/nightly job; diff shows dataset changes before backtests run. |
| Data validation CLI | Implement `scripts/validate_dataset.py --path ...` leveraging `pandas` to detect gaps, duplicate timestamps, timezone drift, and column outliers. Persist report under `results/data_quality/DATE.json`. | Data/Infra | Manifest generator | Validation added as pre-hook to `scripts/backtest_strategy.py`; run fails if severity ≥ “error”. |
| Automated OANDA ingest pipeline | Wrap `get_candles.py` into `scripts/ingest_oanda.py` with argparse + schedule file, logging to `metrics/ingest.log`, and optional retry/backoff. | Data/Infra | API credentials | Nightly job populates latest candles and writes success status into manifest. |
| Data quality scorecard | Extend backtest runner to read latest validation report and attach summary (missing bars %, outliers) into `results/<run_id>/data_report.json`. | Research | Validation CLI | Each backtest folder contains equity, trades, stats, and data_report; PRs check for regressions. |
| Process documentation | Update `docs/data_pipeline.md` with fetch, validation, manifest, storage, and rollback steps. | Ops | Completion of above tasks | Document reviewed + linked from roadmap; new hires can repro pipeline in ≤30 min. |

## KPI Monitoring & Instrumentation Plan
1. **Extend analytics module**: upgrade `metrics/perf.compute_metrics` to output Sortino, Calmar, rolling max drawdown duration, and recovery time; add `metrics/perf.compute_var` (historical/parametric VaR) and integrate with trade logs.  
2. **Standardize run outputs**: modify `scripts/backtest_strategy.py` to emit `results/<run_id>/metrics.json` containing KPIs + data quality hash + config fingerprint; update `charts/` scripts to consume JSON for dashboards.  
3. **Live telemetry bridge**: implement a lightweight metrics publisher in `QuantTrader` (e.g., pushgateway REST or SQLite) to log order latency, success rate, leverage, and risk-limit hits at run-time; ensure schema aligns with backtest metrics for direct comparisons.  
4. **Alert rules**: codify KPI thresholds (Sharpe, drawdown, VaR, uptime) in an alert config file (YAML) consumed by monitoring scripts; include annotations for escalation path.  
5. **Dashboarding**: create Grafana/Streamlit dashboard reading `metrics/` CSV/JSON to visualize rolling KPIs, VaR bands, and adherence to thresholds; include widgets for “haircut stress” vs. actual.  
6. **Validation hooks**: add CI step that loads latest `results/.../metrics.json` and asserts KPIs remain above minimums before merging config/strategy changes; failing tests block deployment.

## Readiness Definition (“Good Project”)
| Category | KPI | Live-Ready Threshold |
| --- | --- | --- |
| Performance | Annualized Return | ≥ 18% |
|  | Sharpe (net) | ≥ 1.4 |
|  | Sortino | ≥ 2.0 |
|  | Max Drawdown | ≤ 10% |
|  | Recovery Time | ≤ 60 trading days |
| Risk | 99% 1-day VaR | < 8% capital |
|  | Stressed VaR | < 12% capital |
|  | Limit Breaches | ≤ 1 per quarter (auto-resolved) |
| Execution | Order Success Rate | ≥ 98.5% |
|  | Avg Slippage | ≤ 0.2 pip or ≤ 15% of modeled costs |
|  | ACK Latency | < 500 ms avg, < 1 s p99 |
| Operations | Trading Uptime | ≥ 99.5% |
|  | Alert MTTA | < 2 minutes |
|  | Config/Code Rollback Test | Pass within 5 minutes |
| Governance | Change Log Coverage | 100% reviewed + archived |
|  | Backup Success | 100% daily, verified weekly |

Meeting the above KPIs—validated by backtest, paper trading, and staged live deployment—constitutes an “excellent, live-ready” status for the project. Continuous monitoring ensures regression is caught early; any KPI falling outside tolerance triggers remediation before further scaling.
