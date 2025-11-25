# Parallel Fence Runbook（Demo + Live 双轨）

## 1. 准备环境变量
1. 复制 `QuantResearch/.env.example` 至仓库根：  
   ```
   cp QuantResearch/.env.example .env.demo
   cp QuantResearch/.env.example .env.live
   ```
2. 在 `.env.demo` 中填写 `OANDA_ACCOUNT_ID_DEMO/OANDA_TOKEN_DEMO/OANDA_URL_DEMO`（practice 账户）；`.env.live` 中填写 LIVE 账号。
3. 运行脚本前，通过 helper 命令导出：
   ```bash
   # demo 会话
   source .env.demo
   export OANDA_ACCOUNT_ID=$OANDA_ACCOUNT_ID_DEMO
   export OANDA_TOKEN=$OANDA_TOKEN_DEMO
   export OANDA_URL=$OANDA_URL_DEMO
   export OANDA_ENVIRONMENT=practice

   # live 会话
   source .env.live
   export OANDA_ACCOUNT_ID=$OANDA_ACCOUNT_ID_LIVE
   export OANDA_TOKEN=$OANDA_TOKEN_LIVE
   export OANDA_URL=$OANDA_URL_LIVE
   export OANDA_ENVIRONMENT=live
   ```
4. 监控相关变量（`SLACK_RISK_WEBHOOK`, `PUSHGATEWAY_URL` 等）可保持一致，确保告警发往同一渠道。

## 2. 启动 Demo / Live 流水线
- **Demo（paper）**：  
  ```
  source .env.demo
  python QuantTrader/scripts/paper_trade.py --config QuantTrader/config/risk_limits_sim.yaml
  ```
- **Live（real, 小额资金）**：  
  ```
  source .env.live
  python QuantTrader/scripts/live_trade.py --config QuantTrader/config/risk_limits.yaml
  ```
- 推荐分别在两个 tmux/screen 会话或 systemd service 中运行，日志写入 `QuantTrader/logs/paper.log` 与 `.../live.log`。

## 3. 对账与 KPI 采集
1. 每个进程都会把成交写入 `results/execution/{paper,live}/fills.csv`（如未启用，请在策略执行器里落盘）。
2. 运行 `python scripts/compare_fills.py --paper results/execution/paper/fills.csv --live results/execution/live/fills.csv`，输出 slippage、fill-rate、latency 差异表。
3. 将 `compare_fills` 结果附加至 `results/risk/metrics.csv` 或单独的 `results/execution/tca_<date>.json`，以便监控端引用。

## 4. 并行监控
- `scripts/watch_ops_metrics.py` 默认读取最近一行指标；建议在 parallel fence 期间添加 `--context live` 参数（后续改造）或维护两份 CSV。
- Grafana Risk dashboard 可以通过 label `run="paper_<id>" / run="live_<id>"` 区分，告警阈值沿用 Phase 4。

## 5. 故障处理
- 若 demo 与 live 差异超过阈值（slippage > 2 bps、rejects>0 等），参考 `docs/runbook_ops.md` 和 `docs/runbook_data_incidents.md` 中的 SOP：暂停 live、调查数据/风控、更新 Failure log。
- 将每次异常记录在 `logs/parallel_fence_issues.md`（可自建），并在 Phase 5 ramp gate 审核时回顾。

完成以上步骤后，即可实现“demo+实盘”并行运行，为 Phase 5 资本爬坡提供对照数据。今后若需要自动化，可把 `source .env.*` 与启动命令封装到 `bin/run_parallel.sh` 中。
