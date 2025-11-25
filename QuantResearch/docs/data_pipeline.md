# Data Pipeline Playbook

Phase 1 的目标是在“抓取 → 存储 → 校验 → 回测”之间建立完全可追踪的链路。本手册说明如何操作、验证以及回滚。

## 1. 数据抓取
- 推荐使用自动化脚本 `scripts/ingest_oanda.py`，可单次拉取或基于 `config/ingest_schedule.yaml` 批量执行，示例：

  ```bash
  python scripts/ingest_oanda.py --symbol EUR_USD --granularity H1 --days 365 --target-count 8000
  # 或使用调度清单
  python scripts/ingest_oanda.py --schedule config/ingest_schedule.yaml
  ```
- 脚本会自动重试、将结果写入 `metrics/ingest.log`（JSON 行）以及 `metrics/ingest_status.json`（每个 symbol 的最新状态），并在成功后调用 `build_dataset_manifest.py` 更新哈希，还会把运行记录追加到 `metrics/ingest_metrics.csv`。
- 生产环境可通过 `bin/run_ingest.sh` + cron 调度，脚本会将标准输出写入 `logs/ingest_cron.log`；失败会触发非零退出编码，可接入告警。
- 常见错误：
  - **API 限速**：脚本会自动指数退避，仍失败时请减少 schedule 条目或拆分时间段。
  - **网络中断**：可重跑同一 schedule，脚本会覆盖目标 CSV。
  - **OANDA Token 过期**：确保 `OANDA_TOKEN` 环境变量有效，必要时重新导入 `.env`。

## 2. 生成数据 manifest
- 用 `scripts/build_dataset_manifest.py` 扫描 `data/raw`、`data/derived`，记录行数、字段、时间范围、SHA256 等元数据：

  ```bash
  python scripts/build_dataset_manifest.py --dirs data/raw data/derived --output data/_manifest.json
  ```
- Manifest 受版本控制，可用于比较不同提交间的数据是否发生变化。每个条目包含：
  - `path`, `rows`, `columns`, `dtypes`
  - `time_start`, `time_end`
  - `sha256`

## 3. 数据校验与质量报告
- 使用 `scripts/validate_dataset.py` 对单个 CSV 运行缺口、重复时间戳、异常值检查，并输出报告至 `results/data_quality/`：

  ```bash
  python scripts/validate_dataset.py --path data/raw/EURUSD_H1.csv
  ```

  结果会给出 `severity`（pass/warn/error）、重复条数、gap 比例、空值计数、数值异常列等。如果 `severity=error`，需要重新抓取或修复数据。
- 报告中会自动附上 manifest 信息（哈希、时间范围），便于追踪。

## 4. 回测前自动 gating
- `scripts/backtest_strategy.py` 现内置数据校验。运行回测会先读取 manifest 并生成校验报告：

  ```bash
  python scripts/backtest_strategy.py --csv_path data/raw/EURUSD_H1.csv
  ```
- 若校验 severity=error，回测会立即终止并输出原因；severity=warn 会继续执行但在日志中提示。
- 每次回测结束后会把校验报告写入 `data/outputs/stats/data_reports/data_<symbol>_<fast>x<slow>_<suffix>.json`，与绩效统计一同保存，确保结果可复现。
- 执行 `CSVFeed` 会自动根据 close/volume 的 z-score（默认阈值 5）给 bar 加 `outlier` 标签，回测可通过 `--skip-outlier-entries`（或 YAML `skip_outlier_entries: true`）忽略这些极值 bar 的新开仓，既保留数据又减小异常对策略的冲击。

## 5. 常见故障与回滚
- **Manifest 缺失/过期**：重新运行 manifest 脚本并提交；若需回滚到旧数据，按照 git 历史恢复对应 CSV + manifest。
- **校验发现 gaps**：确认抓取窗口是否覆盖交易日，必要时补抓并合并；重新生成 manifest 与报告。
- **Outliers/warn**：检查是否因宏观事件或数据错误，若确认属于真实行情，可在报告备注中说明。
- **回测失败（data validation error）**：修复数据后重新运行 `validate_dataset.py`，确保 severity≤warn，再执行回测。
- **数据缺失/哈希漂移**：参见 `docs/runbook_data_incidents.md`，按 Runbook 步骤定位和回滚。

## 6. 监控与可视化
- `python scripts/aggregate_data_quality.py --print` 会生成 `metrics/data_quality_summary.csv` 并在控制台输出最新的 severity/gap；CI 已自动调用。
- `python scripts/watch_quality.py --hours 24` 可作为日常守护，发现 warn/error 时返回非零并（可选）调用 webhook。
- `streamlit run apps/data_quality_dashboard.py` 提供可视化面板（趋势、分布、指标），默认读取 `results/data_quality/`。

后续还需将抓取脚本升级为自动化 ETL，但以上步骤已满足 Phase 1 “数据可追溯 + 校验 gating + 报告入仓” 的要求。把本手册链接到 `live_trading_roadmap.md` 以便团队查阅。

## 7. 压力场景目录（Phase 2）
- 为了让批量回测、Monte Carlo 以及未来 CI 使用一致的压力假设，在 `config/stress_scenarios.yaml` 中维护所有可引用的场景。每个场景必须以 `scenario_name: { ... }` 的形式定义，可用字段包括：
  - `stress_cost_spread_mult`, `stress_cost_comm_mult`, `stress_slippage_mult`: 对应成本/滑点的乘数（≥0）。
  - `stress_price_vol_mult`: 回测引擎的价格波动放大倍数。
  - `stress_skip_trade_pct`: 随机跳过信号的概率，0–1 之间。
  - `return_scale`, `block_size`: Monte Carlo 专用，分别控制收益缩放与 block bootstrap 的默认区块大小。
  - `description` / `notes`: 描述或备注文本。
- 使用 `python scripts/validate_stress_scenarios.py --path config/stress_scenarios.yaml` 验证文件结构与数值范围，若有非法字段或越界值脚本会直接返回非零退出码。
- 回测/模拟脚本（`run_batch_backtests.py`, `run_monte_carlo.py`, `run_walkforward.py` 等）接入 `--scenario` 后即可引用该文件，从而在任何环境中得到相同的压力参数与可追溯标签。

## 8. 诊断图表（Phase 2）
- 运行 `python scripts/plot_backtest_diagnostics.py --batch-csv data/results/batch_backtests_*.csv --walkforward-csv results/<run>/walkforward/metrics.csv --mc-summary results/<run>/stress/mc_summary.json --mc-iterations results/<run>/stress/mc_iterations.csv --equity-csv results/<run>/equity.csv --underwater-csv results/<run>/stats/underwater.csv --out charts/latest --facet-scenario`。
- 输出位于 `charts/diagnostics_<timestamp>/`，包含：
  - `heatmap_*.png`（支持 scenario 分面）；
  - `monte_carlo_box.*`；
  - `walkforward_timeline.*`；
  - `equity_curve.*` 与 `underwater_curve.*`；
  - `diagnostics_metadata.json`（记录输入路径与产物）、`diagnostics_data.json`（指标快照，供仪表板加载）。
- CI/Nightly 可调用 `scripts/run_ci_diagnostics.sh` 自动生成并上传图表，作为 Phase 2 可视化验收的守门环节。
- `make diagnostics`（或 `scripts/run_ci_diagnostics.sh latest`）会自动寻找最新的 batch/walkforward/Monte Carlo 结果，若缺失则回退到 fixtures；配合 `.github/workflows/diagnostics.yml` 每日生成 `charts/ci/diagnostics_*` 供 PR 审阅。想要交互式浏览，可运行 `streamlit run apps/diagnostics_dashboard.py`，侧栏可选择不同 base 目录/场景，内嵌展示生成的 PNG 与指标快照。
- 在部署层面，可以将 `apps/diagnostics_dashboard.py` 以 `streamlit run apps/diagnostics_dashboard.py --server.port 8501 --server.baseUrlPath /diagnostics` 形式挂到内网/Streamlit Cloud，并将 `charts/ci/latest`（指向最近一次 CI 产物的符号链接）作为默认 base 目录；CI 成功后可通过 GitHub API 在 PR 评论中附上 “Diagnostics dashboard: <URL>?base=charts/ci/latest” 便于 reviewer 直接跳转。

## 9. 执行/风控仿真（Phase 3）
- 通过 `python scripts/simulate_execution.py --trades-csv results/<run>/trades.csv --risk-limits-yaml QuantTrader/config/risk_limits.yaml --run-id <run>` 可将单次回测的交易信号注入 `RiskEngine + MockAdapter`，自动生成 `results/execution/<run_id>/sim_results.json / fills.csv / rejects.csv`，同时把风控事件写入 `results/risk/events.jsonl`。
- 若只想跑固定订单，可提供 `--orders QuantTrader/config/orders_sample.csv --risk-config QuantTrader/config/risk_config_sample.json`。
- 风控事件可通过 `python scripts/risk_report.py --log results/risk/events.jsonl --out results/risk/report.csv` 汇总，便于在 PR / Nightly 中审阅拒单、kill-switch 次数。
- CI 守门：运行 `python scripts/check_risk_report.py --report results/risk/report.csv --max-rejects 0 --max-kill 0`，若超过阈值直接退出 1，确保风险仿真不过关时禁止合并/上线。
- GitHub Actions 可以直接调用 `scripts/run_risk_sim.sh`（默认使用 paper adapter + fixtures 或指定 `RUN=<run_id>`），脚本内部会顺序执行 `simulate_execution -> risk_report -> check_risk_report` 并在任一环节失败时终止；推荐在 `.github/workflows/risk-sim.yml` 中 nightly 触发并上传 `results/execution/<run_id>/` 与 `results/risk/report.csv` 供复盘。
