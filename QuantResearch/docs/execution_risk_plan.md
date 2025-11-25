# Execution & Risk Plan (Phase 3)

## Objectives
- Provide deterministic, fault-tolerant order routing between strategy signals and broker APIs.
- Embed real-time risk checks (exposure, leverage, drawdown, kill-switch) before any order hits the wire.
- Maintain identical config schema across backtest, paper, and live modes.

## Execution Adapter Blueprint
| Component | Responsibility | Notes |
| --- | --- | --- |
| `ExecutionAdapter` (abstract) | submit/cancel/modify orders, query positions | Interface at `QuantTrader/execution/adapter.py` |
| `OandaAdapter` / `IBAdapter` | Concrete broker drivers | `QuantTrader/execution/oanda_adapter.py` (practice env) handles retries + idempotent IDs |
| `OrderStore` | Persist pending/fill state for recovery | JSONL store at `results/execution/orders.log` |
| `LatencyMonitor` | Track ack/latency metrics, emit to `metrics/execution.csv` | Reused in alerts |

### API Sketch (Python pseudo)
```python
class ExecutionAdapter(Protocol):
    def submit(self, order: OrderParams) -> OrderAck: ...
    def cancel(self, order_id: str) -> CancelAck: ...
    def sync_positions(self) -> PositionState: ...
```
Each method returns structured responses with timestamps, status, and error codes to feed monitoring.

## Risk Engine Blueprint
| Guard | Trigger | Action |
| --- | --- | --- |
| Exposure limit | `abs(notional)` > per-strategy or account threshold | Reject order + log event |
| Max leverage | portfolio leverage > limit | Auto hedge or flatten |
| Intraday loss cap | Unrealized + realized loss < -X | Trigger kill-switch, notify |
| Drawdown streak | Number of losing trades > N | Reduce position sizing |

Implementation lives at `QuantTrader/core/risk/risk_engine.py`, invoked before each adapter call and during periodic audits (e.g., every minute via scheduler).

## Testing Strategy
- Unit tests (`tests/core/risk/test_limits.py`) with synthetic trade/order streams verifying limit breaches produce expected actions.
- Integration harness `scripts/simulate_execution.py` (orders CSV + risk JSON) replays fills through `MockAdapter` + `RiskEngine`, producing `results/execution/sim_results.json` for inspection.
- Real-run replay：`RUN=<run_id> ./scripts/run_risk_sim.sh` 会读取 `results/<run_id>/summary.json` 中的 `artifacts.trades`，自动定位 `data/outputs/trades/*.csv`，以 PaperAdapter + 实际风险限额重放全部交易；输出写入 `results/execution/<run_id>/`、`results/risk/events.jsonl`、`results/risk/report.csv`。
- Risk event reporting：`python scripts/risk_report.py --log results/risk/events.jsonl` 汇总 reject/kill-switch，用于审阅或接入监控；配合 `python scripts/check_risk_report.py --report results/risk/report.csv --max-rejects 0 --max-kill 0` 可在 CI/Nightly 自动守门。
- CI 守门：`.github/workflows/risk-sim.yml` 每晚 04:00 UTC（及手动 dispatch）执行，自动寻找最新 `results/<run_id>/` 重放；若发现限制被触发则上传 `results/risk/report.csv` / `events.jsonl` 并让 workflow 失败，提醒在 PR 中修复或更新限额。
- Chaos drills: scripted failure injection (API downtime, delayed ACKs) to ensure retry + kill-switch logic holds.

## Dependencies & Next Steps
1. **Metrics** – ensure Phase 2 outputs include per-trade latency/signal metadata；adapter + `OrderStore` feed `metrics/execution.csv`.
2. **Secrets/Env** – document broker credentials handling (vault or `.env`) before wiring live endpoints.
3. **Paper Harness** – build a sandbox runner that replays market data and records simulated fills for 30-day paper validation.
4. **Runbooks** – extend `docs/runbook_data_incidents.md` with execution/risk incident procedures (flatten, failover, restart).

This plan becomes the kickoff brief for Phase 3 once Phase 2 visualization gates are signed off.
