# Go-Live Runbook (Phase 5)

## 1. 预检 (T-1)
1. **数据完整性**：`python scripts/build_dataset_manifest.py ...` → `python scripts/validate_dataset.py ...`，确认 `severity <= warn`。
2. **风险限额**：审阅 `QuantTrader/config/risk_limits.yaml`，确认每日/总曝险、最大回撤设置符合资本规模。
3. **策略参数**：记录将上线的策略配置（YAML hash + git commit），保存到 `docs/change_log.md`。
4. **API / 凭证**：刷新 `.env.live`，验证 `OANDA_ACCOUNT_ID_LIVE/OANDA_TOKEN_LIVE` 有效；在 1Password 更新“Live Trading”条目并记录轮转时间。
5. **监控链路**：打开 Grafana Risk dashboard + Slack `#risk-alerts`，执行 `python scripts/watch_ops_metrics.py --max-latency-ms 1` 触发一次测试并确认能恢复。

## 2. 开盘前 Checklist (T-0, -30min)
1. `source .env.demo` / `.env.live`，分别启动 paper 与 live 进程（参见 `docs/parallel_fence.md`）。  
2. `tmux new -s live_trade` 中运行：  
   `python QuantTrader/scripts/live_trade.py --config QuantTrader/config/risk_limits.yaml`  
   paper 流程同理但加载 `risk_limits_sim.yaml`。  
3. 验证数据流：观察日志是否持续收到 tick/行情；若 2 分钟无数据，立刻停止排查网络/API。  
4. 手动触发 `./scripts/notify_risk_metrics.sh`，确认最新 run_id 显示状态 pass。  
5. 通知 on-call + 风控：在 Slack `#risk-alerts` 发送 “Live session starting @ <时间>，资本上限 <X>”。

## 3. 盘中监控
| 频率 | 项目 | 工具/路径 | 阈值/动作 |
| --- | --- | --- | --- |
| 5 min | Grafana Risk Metrics | `https://grafana.internal/d/risk-metrics` | rejects>0、latency>500ms、live Sharpe<1.4，立即降级至 paper-only |
| 15 min | compare_fills | `python scripts/compare_fills.py --paper ... --live ... --out ...` | `|pnl_diff_mean| > $50` 或 `latency_gap>50ms` → 通知风控 |
| 实时 | Slack 告警 | `#risk-alerts` | 收到 fail 立即执行 Incident SOP |

## 4. 收盘后 (T+0)
1. 停止 live/paper 进程（Ctrl+C 或 `systemctl stop quant-live.service`）。  
2. 运行 `QuantTrader/bin/post_session.sh <run_id>`（封装 compare_fills → update_metrics_from_tca → watch_ops → Prom push），脚本输出：  
   - `QuantTrader/results/execution/tca_summary.json`  
   - 追加 `run_id` 行至 `QuantResearch/results/risk/metrics.csv`  
   - `scripts/watch_ops_metrics.py` / `export_metrics_prom.py` 日志  
   若需要手工执行，可参考脚本内命令。  
4. 在 `docs/runbook_paper_risk.md` 的 Failure log 填写当日摘要：PnL、最大曝险、告警次数、计划内/外事件。  
5. 若出现 incident，创建 `docs/postmortem/<date>_<topic>.md`，按照模板记录根因与动作。

## 5. Capital Ramp Gate
1. 维护滚动 30 日窗口（`rolling_sharpe_30d`, `live_drawdown_pct`, `slippage_bps`），由 `watch_ops_metrics.py` 与 Grafana 守门。  
2. 满足条件（Sharpe ≥ 1.4，live drawdown < 5%，slippage < 2 bps，rejects=0）满 6 周后，提交 “Ramp Gate” 复核：  
   - 附上 `tca_summary.json`、`results/risk/metrics.csv` 摘要、Grafana 截图。  
   - 风控 & 研究双签字；批准后更新 `docs/live_trading_roadmap.md` 的资本表。
3. 若任一 KPI 失守：立即回退到上一资金档，并记录在 Failure log。

## 6. 应急回退
1. **硬停**：`./scripts/notify_risk_metrics.sh` 报错或 Grafana 告警无法恢复 -> `killall python` 或 `systemctl stop quant-live.service`。  
2. **仓位平仓**：使用 broker GUI 或 `QuantTrader/scripts/flat_positions.py`（若存在）直接平仓；记录执行时间与结果。  
3. **通知**：Slack 发布 incident + 邮件 `ops-oncall@company.com`。  
4. **分析与复盘**：24h 内完成 postmortem，更新 `docs/runbook_ops.md`/roadmap。

本 Runbook 将随 Phase 5 迭代持续更新；所有修改须在 PR 中 reviewer 审核并与风控确认。
