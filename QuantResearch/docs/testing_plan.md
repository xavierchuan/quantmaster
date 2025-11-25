# Testing & Validation Plan

## Phase 1 – Data & Research Stack
| Area | Test Type | Tool / Script | Trigger | Pass Criteria |
| --- | --- | --- | --- | --- |
| 数据抓取/ETL | 单次/批量回归 | `scripts/ingest_oanda.py --dry-run`, `bin/run_ingest.sh` | 更新 ingest 脚本、调度配置、API 密钥 | 脚本退出码 0，`metrics/ingest.log` 写入 success，manifest 刷新成功 |
| Manifest/数据签名 | CLI 回归 | `python scripts/build_dataset_manifest.py ...` + `python scripts/check_data_integrity.py` | 数据目录变更、PR pre-check、夜间任务 | 所有监控数据哈希与 baseline 一致、severity≠error |
| 数据质量 | 单元/CLI | `python scripts/validate_dataset.py --path ...` | 新数据导入、回测前门禁 | severity<=warn、报告存入 `results/data_quality` |
| 回测 gating | 集成测试 | `python scripts/backtest_strategy.py --csv ...` | 关键配置/代码变动 | data validation 通过、`results/<run>/summary.json` 生成 |

## Phase 2 – Backtest & Simulation Enhancements
| Area | Test Type | Tool / Script | Trigger | Pass Criteria |
| --- | --- | --- | --- | --- |
| KPI 计算 | 单元 | `python -m pytest metrics/tests/test_perf.py`（TODO） | `metrics/perf.py` 变更 | Sortino、Calmar、duration 指标与基准数据一致 |
| 策略引擎参数 | 集成 | `python scripts/backtest_strategy.py --config sample.yaml` | `StrategyEngine` / `strategies` 逻辑更新 | 运行成功，`validate_results.py`通过，参数快照记录正确 |
| 多策略/成本模型 | 集成 | `python scripts/backtest_strategy.py --config ... --strategy-mode weighted ...` | 增删策略/组合 | Weighted 运行指标符合预期，cost profile 切换生效 |
| Stress Hooks | 集成 | `python scripts/backtest_strategy.py ... --stress-* ...` | 压力参数或实现变更 | 运行成功、日志显示 stress 生效、结果被 `validate_results.py` 接受 |
| Batch/Grid | 集成 + 单元 | `python scripts/run_batch_backtests.py --schedule ... --scenario base --scenario-file config/stress_scenarios.yaml`; `python -m unittest tests.test_run_batch_backtests` | 批量工具修改 | CSV/JSON 输出 run_id + KPI，并记录 scenario 及 stress 参数列（spread/comm/slippage/skip 等），所有 run 通过 `validate_results.py` |
| Monte Carlo/Walkforward | 集成 + 单元 | `python scripts/run_monte_carlo.py --run results/<id> --iterations 200 --method bootstrap --scenario base --scenario-file config/stress_scenarios.yaml`; `python -m unittest tests.test_run_monte_carlo`; `python scripts/run_walkforward.py --config ... --train-bars ... --test-bars ...`; `python -m unittest tests.test_run_walkforward` | 模拟脚本变更 | Monte Carlo：`results/<run>/stress/mc_summary.json` + `mc_iterations.csv` 生成且记录 Sharpe/Sortino/Calmar 百分位、`p_ruin`、scenario/seed 元数据；Walk-forward：`results/.../walkforward/metrics.csv` + `summary.json` 产出，包含每窗口 KPI/数据哈希/参数指纹，并按阈值标记 pass/fail |
| Diagnostics 可视化 | 集成 + 单元 | `python scripts/plot_backtest_diagnostics.py --batch-csv ... --walkforward-csv ... --mc-summary ... --mc-iterations ... --out charts/test`; `python -m unittest tests.test_plot_backtest_diagnostics` | Phase 2 报告/图形逻辑调整 | `charts/diagnostics_*/` 目录生成 heatmap、box plot、timeline 等图表，`diagnostics_metadata.json` 记录输入/产物，文件可供审阅或上传 |

## Phase 3 – Execution & Risk Layer
| Area | Test Type | Tool / Script | Trigger | Pass Criteria |
| --- | --- | --- | --- | --- |
| Execution Adapter | 集成/模拟 | 模拟 API、回放（TODO: `scripts/simulate_execution.py`） | 接口/风控改动、券商 API 版本升级 | 拒单率、延迟、人为故障注入时行为符合 SLA |
| Portfolio/Risk Engine | 单元 + 集成 | `QuantTrader/core/risk/tests`, paper 账户回测 | 新增限额/计算逻辑 | 预期风控触发准确、kill-switch 生效、日志完整 |
| Paper vs Live 对账 | 集成 | `scripts/compare_fills.py` | 执行模块更新、券商配置调整 | 仿真成交价/实盘偏差在容忍范围 | 
| Stress Drills | 演练 | 触发命令、故障注入 | 季度演练、重大发布前 | Runbook 操作符合 SLA，系统可恢复 |

## Phase 4 – Monitoring, Ops & Security
| Area | Test Type | Tool / Script | Trigger | Pass Criteria |
| --- | --- | --- | --- | --- |
| 监控指标 | 单元/集成 | Prometheus/Grafana 检查、自检脚本 | 新增指标、告警规则改动 | 指标延迟 < 1m，数据完整 |
| 告警路径 | Drill | `scripts/watch_quality.py`, 伪造故障 | 新告警上线、季度审查 | 告警能送达、确认流程记录完备 |
| Runbook | 演练 | Tabletop/实际演练 | Runbook 更新、重大版本前 | 操作步骤准确、恢复时间达标 |
| 安全/合规 | Audit | 权限/密钥扫描脚本 | 密钥轮换、权限变更 | 所有密钥加密、访问日志留痕 |

## Phase 5 – Go-Live & Scaling
| Area | Test Type | Tool / Script | Trigger | Pass Criteria |
| --- | --- | --- | --- | --- |
| 平行跑 | 集成 | Paper vs Live 对账脚本 | 每次资金放大 | 偏差≤阈值，风险指标无异常 |
| Pre-flight Checklist | 手动/自动 | `docs/runbook_data_incidents.md` +脚本化检查 | 每日开盘前 | 所有清单项“通过” |
| Post-trade TCA | 分析 | `scripts/post_trade_tca.py`（TODO） | 日终、策略更新后 | 滑点/成本与模型偏差可解释 |
| Incident Review | 流程 | 复盘模板 | 每次告警/故障 | 24h 内完成 RCA，并更新 Runbook/监控 |

| Area | Test Type | Tool / Script | Trigger | Pass Criteria |
| --- | --- | --- | --- | --- |
| Execution Adapter | 集成/模拟 | 模拟 API、回放 | 接口/风控改动 | 拒单率、延迟指标满足 SLA |
| Risk Engine | 集成 | 风控单元测试 + Paper账户 | 限额/规则改动 | 触发 kill-switch、日志完整 |
| Monitoring/Alert | Chaos/Drill | `scripts/watch_quality.py`, 手动注入故障 | 告警配置变更、季度演练 | 告警在 SLA 内触发，Runbook 操作可复原 |

## Test Automation Hooks
- **CI**：`.github/workflows/data-integrity.yml` 执行 `check_data_integrity.py`、`validate_dataset.py`、`aggregate_data_quality.py` 并上传报告。
- **Pre-commit (建议)**：将 `python scripts/build_dataset_manifest.py ...`、`check_data_integrity.py`、`validate_results.py` 加入 pre-commit 钩子，防止未验证结果入库。
- **Nightly**：调度 `bin/run_ingest.sh`、`scripts/watch_quality.py --hours 24 --webhook ...`，并汇总 `metrics/data_quality_summary.csv`。
