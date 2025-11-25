# Ops Runbook

## Slack & Alerting
- Channel: `#risk-alerts`
- Webhook: configure `.env` with `SLACK_RISK_WEBHOOK` (see `.env.example`)
- Server path: `/srv/QuantResearch`
- Cron command:
  ```
  */30 * * * * cd /srv/QuantResearch && source .env && ./scripts/notify_risk_metrics.sh >> logs/risk_notify.log 2>&1
  ```
- Fail SOP:
  1. Check `results/risk/events_<run>.jsonl` for reason (exposure/latency/etc.)
  2. Adjust `QuantTrader/config/risk_limits.yaml` or strategy sizing if needed
  3. `RUN=<run_id> ./scripts/run_risk_sim.sh && ./bin/backfill_risk.sh`
  4. Confirm diagnostics CI + Slack alert returns to green; note resolution in Failure log

## Prometheus / Grafana
- Metrics export script: `python scripts/export_metrics_prom.py`
- CI pushes to Pushgateway (`$PUSHGATEWAY_URL/metrics/job/risk_sim`)
- Grafana dashboard: “Risk Metrics” -> rejects/latency/PnL/exposure -> alert rules:
  - rejects > 0 (critical)
  - latency_ms_avg > 500 (warning)
  - total_pnl < -3000 (warning)
- Dashboard URL: `https://grafana.internal/d/risk-metrics` (bookmark + keep updated when endpoint changes)
- Login: SSO group `ops-risk`; fallback service account `risk-dashboard` (credentials stored in 1Password entry “Grafana Risk Dashboard”)
- Alert contacts: Slack `#risk-alerts`, on-call alias `ops-oncall@company.com`, phone escalation `+86-139-0000-0000`
- Ensure Grafana → `#risk-alerts` routing:
  1. In Grafana **Alerting → Contact points**, create/update “Risk Slack” contact with the same webhook URL stored in `.env` (`SLACK_RISK_WEBHOOK`).
  2. Under **Notification policies**, set default policy (or risk subtree) to use “Risk Slack”.
  3. Edit each Risk Metrics rule (rejects/latency/PnL/drawdown/exposure) so the notification channel is “Risk Slack”; test via “Send test notification” to confirm it reaches `#risk-alerts`.

### Prometheus / Grafana cron + CI (Last updated: 2025-11-11 14:30 CST)
- **Server cron push:**  
  `*/15 * * * * cd /srv/QuantResearch && source .env && python scripts/export_metrics_prom.py --csv results/risk/metrics.csv --job risk_sim | curl --fail --data-binary @- "$PUSHGATEWAY_URL/metrics/job/risk_sim" >> logs/prom_push.log 2>&1`  
  - Latest manual verification: `tail -1 logs/prom_push.log` on 2025-11-11 returns `push OK`.
- **CI diagnostics:** `.github/workflows/diagnostics.yml` 有 “Push risk metrics to Prometheus” 步骤（`python scripts/export_metrics_prom.py … | curl …`），运行条件 `always()` + `PUSHGATEWAY_URL` secret。
- **Grafana dashboard:** `https://grafana.internal/d/risk-metrics`，Contact point “Risk Slack”，告警阈值：rejects>0、latency>500ms、total_pnl<-3000、drawdown>0.1、exposure>2e6、rolling_sharpe_30d<1.4、live_latency_p95>500ms、slippage_bps>2。面板最后校验时间 2025-11-11（见 dashboard “Updated at”）。
- **Prometheus target:** `pushgateway` job scraping `http://localhost:9091/metrics`。验证方法：  
  `curl http://localhost:9090/api/v1/query?query=risk_total_pnl{job="risk_sim"}`；若返回非空即表示采集正常。
- **Alert drill log:** 2025-11-11 14:35 CST 手动追加 `run_id=synthetic_fail`（rejects>0, status=fail）触发 `scripts/notify_risk_metrics.sh` → Slack `#risk-alerts`/Grafana 告警均收到；随后移除测试行、重推 Prometheus 并确认恢复。

## Secrets
- `.env.example` lists required variables (`SLACK_RISK_WEBHOOK`, `PUSHGATEWAY_URL`, etc.)
- For production, store `.env` with correct values and restrict permissions.
- GitHub Actions: repository secret `PUSHGATEWAY_URL` is required so `diagnostics.yml` can push metrics to Prometheus; keep it in sync with server-side `.env`.

## Incident Checklist
1. Alert triggered (Slack or Prometheus)
2. Consult `docs/runbook_paper_risk.md` failure log for similar cases
3. Run diagnostics (`./scripts/run_ci_diagnostics.sh`) if local debugging needed
4. Update runbook log with resolution before closing incident；必要时按照 `docs/postmortem_template.md` 记录正式 incident。
