# Runbook – 数据事故处理

## 1. Manifest/哈希漂移
1. 运行 `python scripts/check_data_integrity.py --baseline data/signature_baseline.json`。
2. 若输出 hash 不一致：
   - 确认是否有合法的数据刷新（查看 `metrics/ingest.log`、`metrics/ingest_status.json`）。
   - 比对旧版 CSV（git checkout）与最新 CSV，确认差异。
   - 如属合法更新，重跑 `scripts/build_dataset_manifest.py` 并更新 `data/signature_baseline.json`，同时在 PR 写明原因并附 data_quality 报告。
   - 如属异常，恢复旧 CSV + manifest，并重新 ingest。

## 2. 数据缺口 / validation=error
1. 查看 `results/data_quality/*.json` 中的 `gap_ratio`、`messages`，定位缺口区间。
2. 使用 `scripts/ingest_oanda.py --symbol ... --days ... --output ... --retries ...` 重新抓取指定区间。
3. 若缺口无法补齐（例如交易所停牌），在 data_quality 报告中注明并保持 severity=warn。
4. 重跑 manifest + check_data_integrity，并附 run_id。

## 3. Ingest 失败
1. 检查 `logs/ingest_cron.log` 和 `metrics/ingest.log`，查明失败原因（API 429、网络、认证等）。
2. 若连续失败，使用 `python scripts/ingest_oanda.py --symbol ... --retries 5 --backoff 30` 手动重试。
3. 成功后确认 `metrics/ingest_status.json` 已更新，再触发 manifest。

## 4. 告警处理
1. `scripts/watch_quality.py --hours 24` 将在 warn/error 时返回非零；CI/监控可根据退出码报警。
2. 收到告警后，按上述步骤定位。
3. 处理完成后，在对应 runbook entry 中记录 root cause、fix、防再发措施。

## 5. Risk Simulation 失败
1. CI/Nightly 中的 `risk-sim.yml` 会运行 `scripts/run_risk_sim.sh`（模拟执行 → `risk_report` → `check_risk_report`）。若 workflow 失败或 `check_risk_report.py` 超阈值：
   - 下载 workflow artifact `risk-sim-results`，检查 `results/risk/report.csv` 以及 `results/risk/events.jsonl`。
   - 关注 `event` 字段为 `reject`/`kill_switch` 的策略与原因（例如 `symbol_exposure_limit`、`drawdown_limit`）。
2. 若为合法策略调整导致的限制触发：
   - 更新 `QuantTrader/config/risk_limits.yaml`，提交 PR 并说明理由，再次运行 `scripts/run_risk_sim.sh RUN=<run_id>` 验证。
3. 若为异常（无意放大仓位/数据错误）：
   - 立即停止相应策略发布，修复数据或参数，重跑回测 + risk-sim，确认 `check_risk_report.py` 通过后才可恢复。
4. 记录事故：在 runbook 追加条目，注明触发时间、策略、阈值、修复步骤、防再发方案。

## 6. Prometheus / Grafana 指标异常
1. **确认数据是否推送**：登录服务器 `/srv/QuantResearch`，运行  
   `source .env && python scripts/export_metrics_prom.py --csv results/risk/metrics.csv --job risk_sim | curl --fail --data-binary @- "$PUSHGATEWAY_URL/metrics/job/risk_sim"`  
   如果命令成功，说明 pushgateway 可写。
2. **检查 Pushgateway**：`curl $PUSHGATEWAY_URL/metrics | grep risk_` 应返回最新 run 的指标；若无，查看 `logs/prom_push.log`（cron）或 CI 日志，修复网络/权限后重新推。
3. **验证 Prometheus**：在 Prometheus UI `Status → Targets` 确认 `pushgateway` 目标为 `UP`。如 `DOWN`，检查 `/opt/homebrew/etc/prometheus.yml` 的 `pushgateway` job 配置并重启服务。
4. **Grafana 告警**：进入 `Risk Metrics` dashboard，确认图表刷新。若告警未触发/未恢复，检查 Alert contact “Risk Slack” 是否指向正确 webhook；必要时在 `docs/runbook_ops.md` 的步骤中重新测试。
5. **记录**：在 incident log 中写明何时发现、推送/采集/告警哪个环节异常、采取的修复措施以及再次验证结果。

## 7. Artifact Retention/清理
1. 任何 run、trades、stats、模型目录若需要长期保留，请在对应 `summary.json` 添加 `retention`（`baseline` / `wf_baseline` / `archive`）并在 `docs/retention.md` 记录。
2. 清理生成物前运行 `python QuantResearch/scripts/cleanup_artifacts.py --results QuantResearch/results --dry-run --prune-data-outputs`，确认待删列表仅包含 `retention=ephemeral` 的 run。
3. 需要真正删除时追加 `--apply`；脚本会顺便同步 `data/outputs/{stats,trades}` 与 `artifacts/models/` 的引用列表。
4. 删除任何 `data/raw/*backup*.csv` 或大型 dataset 前，务必执行 `scripts/build_dataset_manifest.py` + `scripts/check_data_integrity.py`，并在 PR 中说明新的 manifest 哈希。
5. 若误删 baseline，立即从 `archive/` 还原（参考 `docs/retention.md` 的 tar ball 路径）并在 incident log 记录 root cause 与防再发措施。
