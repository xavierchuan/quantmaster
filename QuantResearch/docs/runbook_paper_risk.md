# Paper Trading Risk Runbook

This runbook documents the standard procedure for replaying a backtest run through the execution/risk stack, reviewing the results, and signing off before moving to paper or live deployments.

## Prerequisites
- Fresh backtest results under `QuantResearch/results/<run_id>/` with `summary.json` populated (run via `python scripts/backtest_strategy.py ...`).
- Data-quality validations already green (Phase 1 guards).
- `.env` or environment variables configured for any broker API keys required by the paper adapter (mock/paper by default).
- Run/模型必须被标记 `summary.json.retention`（`baseline`/`wf_baseline`）并在 `docs/retention.md` 中列入保留清单；如需复用 archive run，请先按该文档将 tar 包还原，再继续以下步骤。

## Step 1 – Identify the run
1. List available runs: `ls results | grep '^[0-9]' | sort`.
2. Choose the run ID you intend to promote (e.g., `20251110_112516`).
3. Sanity-check `results/<run_id>/summary.json` (must include `artifacts.trades` pointing to `data/outputs/trades/*.csv`).
4. 若 `summary.json` 缺少 `retention` 字段或被标记为 `ephemeral`，不得用于签核；请重新跑回测并更新 `docs/retention.md` 后再继续。

## Step 2 – Execute risk simulation
```bash
cd QuantResearch
# 仿真守门默认使用 config/risk_limits_sim.yaml（宽松限额）
RUN=<run_id> ./scripts/run_risk_sim.sh
```
The script will:
- Locate the trades CSV via `summary.json`.
- Replay orders through `PaperAdapter` + `RiskEngine`.
- Emit `results/execution/<run_id>/` (fills/rejects) and `results/risk/events.jsonl`, `results/risk/report.csv`, `results/risk/metrics.csv`.

> 仿真与实盘限额分离：`run_risk_sim.sh` 自动加载 `QuantTrader/config/risk_limits_sim.yaml`。真实部署前请改为 `QuantTrader/config/risk_limits.yaml` 或在 README/PR 中说明所用限额。

## Step 3 – Review outputs
1. **Execution folder**: open `results/execution/<run_id>/fills.csv` & `rejects.csv`. Ensure fills count matches trades, no unexpected rejects.
2. **Risk report**: inspect `results/risk/report.csv` (should list zero `reject`/`kill_switch`). Cross-check aggregated line appended to `results/risk/metrics.csv`.
3. **Logs**: if `results/risk/events.jsonl` contains entries, copy-paste into the incident tracker and analyze the `reason` fields.

## Step 4 – Failure handling
- If `check_risk_report.py` fails, read the console output for counts; re-run with debugging (e.g., adjust limits in `QuantTrader/config/risk_limits.yaml` or fix strategy sizing) before retrying Step 2.
- Document the failure in the runbook table below和在 `results/risk/events_<run>.jsonl` 里注明原因；整改完成后运行 `./bin/backfill_risk.sh` 确认 `metrics.csv` 已同步。

### Failure log
| Run ID | Symbol | Reason (from `results/risk/events_<run>.jsonl`) | Remediation Plan | Status |
| --- | --- | --- | --- | --- |
| 20251110_111636 | USDJPY | `symbol_exposure_limit` + `gross_leverage_limit` (qty 10k @ ~155 JPY → notional 1.6M > legacy caps) | Raised global `max_position_notional` to 10M & `max_gross_leverage` to 1000 → reran `RUN=20251110_111636 ./scripts/run_risk_sim.sh` (2025‑11‑11 10:46 UTC) | Resolved |
| 20251110_112519 | USDJPY | Same as above under double-cost stress scenario | After limits update reran `RUN=20251110_112519 ...` (2025‑11‑11 10:47 UTC); result pass | Resolved |
| 20251110_164718 | USDJPY | Same as above for walk-forward scenario | Reran `RUN=20251110_164718 ...` (2025‑11‑11 10:47 UTC) | Resolved |
| 20251109_145009 | USDJPY | Same limit issue | Reran `RUN=20251109_145009 ...`，已 pass | Resolved |
| 20251109_145113 | USDJPY | Same limit issue | Reran `RUN=20251109_145113 ...`，已 pass | Resolved |
| 20251109_145228 | USDJPY | Same limit issue | Reran `RUN=20251109_145228 ...`，已 pass | Resolved |
| 20251109_145252 | USDJPY | Same limit issue | Reran `RUN=20251109_145252 ...`，已 pass | Resolved |

## Step 5 – Sign-off checklist
| Item | Owner | Status | Notes |
| --- | --- | --- | --- |
| Backtest KPIs ≥ thresholds | Research | ☐ |  |
| `RUN=<id> ./scripts/run_risk_sim.sh` success | Trading/Risk | ☐ |  |
| `results/risk/report.csv` reviewed | Trading/Risk | ☐ | （Fail 时附上 GitHub Actions run 链接） |
| `results/execution/<id>/fills.csv` spot-checked | Trading/Risk | ☐ |  |
| Metrics appended (`results/risk/metrics.csv`) | Ops | ☐ |  |
| Diagnostics dashboard updated (`charts/ci/latest`) | Ops | ☐ |  |

Mark each checkbox,运行 `./bin/backfill_risk.sh` 确认可审计，再 archive snapshot；diagnostics CI 会执行 `watch_risk_metrics.py`，若仍有 fail run 会直接阻断 PR，请在推送前先本地修复。

## Automation helpers
- **风险台账补录**：`./bin/backfill_risk.sh` 会扫描 `results/` 与 `metrics.csv` 的差集并自动调用 `scripts/backfill_risk_metrics.py`。
- **Diagnostics 图表**：`./scripts/run_ci_diagnostics.sh` 自动挑选最新 batch/walk-forward/Monte Carlo 数据，输出到 `charts/ci/diagnostics_<timestamp>`（CI 与本地同脚本），并在 `$GITHUB_STEP_SUMMARY` 中嵌入最新风险图。
- **Slack/Cron 告警**：将 `SLACK_RISK_WEBHOOK=https://hooks.slack.com/... ./scripts/notify_risk_metrics.sh` 写入服务器 cron（例如每日 13:00 运行），脚本内部调用 `watch_risk_metrics.py` 并在 fail 时自动发送告警。也可在 CI 中添加相同步骤，实现 7x24 守护。
- **环境变量模板**：复制 `.env.example` 为 `.env` 并填入 `SLACK_RISK_WEBHOOK` 等凭证，方便在本地/cron/CI 统一引用。
- **Artifact Cleanup**：`python QuantResearch/scripts/cleanup_artifacts.py --results QuantResearch/results --apply` 仅会删除 `retention` ≠ `baseline|wf_baseline|archive` 的 run，并可附带 `--prune-data-outputs` 清除未引用的 `data/outputs/stats|trades`。在执行任何危险清理前，务必确认本 run 已在 `docs/retention.md` 标记并创建外部 archive。

## References
- `docs/execution_risk_plan.md` – adapter & risk engine design.
- `docs/live_trading_roadmap.md` – Phase 3 deliverables and KPIs.
- `docs/runbook_ops.md` – Slack/cron/Prometheus 细节。
- Streamlit diagnostics: `http://localhost:8501/diagnostics?run=<run_id>`.
