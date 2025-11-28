"""
Paper trading driver that wires live OANDA pricing into the strategy engine.
"""

from __future__ import annotations

import argparse
import signal
import time
import urllib.request
from dataclasses import asdict
from queue import Empty, Queue

import pandas as pd
from loguru import logger
import yaml
import requests
import urllib.request

import os
import sys

TRADER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(TRADER_ROOT)
RESEARCH_ROOT = os.path.join(REPO_ROOT, "QuantResearch")
sys.path.extend([TRADER_ROOT, REPO_ROOT, RESEARCH_ROOT])

from data.oanda_stream import OandaPricingStream
from core.oanda_execution import OandaExecution
from core.events import TickEvent, OrderEvent
from QuantResearch.core.backtest.strategy_engine import (
    StrategyEngine,
    StrategySpec,
    parse_strategy_specs,
    _coerce_fx_rates,
    _merge_fx_rates,
)
from QuantResearch.core.backtest.ledger import Ledger
from shared.utils.config import OANDA_ACCOUNT_ID, OANDA_TOKEN
from shared.utils.oanda_client import snapshot_account
from utils.kill_switch import KillSwitch
from utils.risk import load_risk_profile, DEFAULT_RISK_PROFILE


class BarAggregator:
    """
    Aggregate tick data into fixed timeframe OHLC bars.
    """

    def __init__(self, symbol: str, timeframe: str):
        self.symbol = symbol.replace("_", "").upper()
        tf = timeframe.lower() if isinstance(timeframe, str) else timeframe
        self.timeframe = pd.to_timedelta(tf)
        if self.timeframe <= pd.Timedelta(0):
            raise ValueError(f"Invalid timeframe: {timeframe}")
        self.current_bucket: pd.Timestamp | None = None
        self.open = self.high = self.low = self.close = None
        self.last_ts: pd.Timestamp | None = None

    def update(self, tick: TickEvent) -> dict | None:
        ts = pd.Timestamp(tick.ts)
        bucket = ts.floor(self.timeframe)
        mid = (tick.bid + tick.ask) / 2.0
        if self.current_bucket is None:
            self._start_bar(bucket, mid, ts)
            return None
        if bucket != self.current_bucket:
            finished = self._build_bar()
            self._start_bar(bucket, mid, ts)
            return finished
        self._update_bar(mid, ts)
        return None

    def flush(self) -> dict | None:
        if self.current_bucket is None:
            return None
        return self._build_bar()

    def _start_bar(self, bucket: pd.Timestamp, price: float, ts: pd.Timestamp) -> None:
        self.current_bucket = bucket
        self.open = self.high = self.low = self.close = price
        self.last_ts = ts

    def _update_bar(self, price: float, ts: pd.Timestamp) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.last_ts = ts

    def _build_bar(self) -> dict:
        bar = {
            "symbol": self.symbol,
            "ts": self.last_ts,
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": 0,
        }
        return bar


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_remote_state(account_id: str, token: str, environment: str, symbol: str) -> dict | None:
    base_url = os.getenv("OANDA_URL")
    if not base_url:
        base_url = "https://api-fxpractice.oanda.com/v3" if environment == "practice" else "https://api-fxtrade.oanda.com/v3"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        summary = requests.get(f"{base_url}/accounts/{account_id}/summary", headers=headers, timeout=5).json()
        positions = requests.get(f"{base_url}/accounts/{account_id}/positions", headers=headers, timeout=5).json()
        pending = requests.get(f"{base_url}/accounts/{account_id}/pendingOrders", headers=headers, timeout=5).json()
    except Exception as exc:
        logger.warning(f"[Reconcile] Failed to fetch remote state: {exc}")
        return None

    account = summary.get("account", {}) if isinstance(summary, dict) else {}
    nav = float(account.get("NAV") or account.get("balance") or 0.0)
    server_time = account.get("lastTransactionID") or summary.get("lastTransactionID")  # fallback placeholder
    remote_positions = {}
    for pos in positions.get("positions", []):
        inst = pos.get("instrument")
        if not inst:
            continue
        qty = float(pos.get("long", {}).get("units", 0.0)) + float(pos.get("short", {}).get("units", 0.0))
        avg = float(pos.get("avgPrice", 0.0) or 0.0)
        unreal = float(pos.get("netUnrealizedPL", 0.0) or 0.0)
        remote_positions[inst.replace("_", "")] = {"quantity": qty, "avg_price": avg, "unrealized_pnl": unreal}

    open_orders = []
    for od in pending.get("orders", []):
        try:
            open_orders.append({
                "id": od.get("id"),
                "instrument": od.get("instrument"),
                "side": "BUY" if float(od.get("units", 0)) > 0 else "SELL",
                "price": float(od.get("price", 0.0) or 0.0) if od.get("price") else None,
                "sl": od.get("takeProfitOnFill", {}).get("price"),
                "tp": od.get("stopLossOnFill", {}).get("price"),
            })
        except Exception:
            continue

    return {"nav": nav, "server_time": server_time, "positions": remote_positions, "open_orders": open_orders}


def build_engine(cfg: dict, args, execution_handler):
    symbol = args.symbol or cfg.get("symbol", "EURUSD")
    account_ccy = cfg.get("account_ccy", "USD")
    fast_win = int(cfg.get("fast", 50))
    slow_win = int(cfg.get("slow", 200))
    spread = float(cfg.get("spread", 1.0))
    slip = float(cfg.get("slip", 0.2))
    comm = float(cfg.get("comm", 2.0))
    qty = float(cfg.get("qty", 10_000))
    initial_cash = float(cfg.get("cash", 100_000))
    stop_loss_pips = cfg.get("sl", 50)
    take_profit_pips = cfg.get("tp")
    atr_sl = cfg.get("atr_sl")
    atr_tp = cfg.get("atr_tp")
    atr_window = int(cfg.get("atr_window", 14))
    regime_ema_window = int(cfg.get("regime_ema_window", 200))
    regime_slope_min = cfg.get("regime_slope_min")
    if regime_slope_min is not None:
        regime_slope_min = float(regime_slope_min)
    regime_atr_min = cfg.get("regime_atr_min")
    if regime_atr_min is not None:
        regime_atr_min = float(regime_atr_min)
    rsi_period = int(cfg.get("rsi_period", 14))
    rsi_long_thresh = cfg.get("rsi_long_thresh")
    if rsi_long_thresh is not None:
        rsi_long_thresh = float(rsi_long_thresh)
    rsi_short_thresh = cfg.get("rsi_short_thresh")
    if rsi_short_thresh is not None:
        rsi_short_thresh = float(rsi_short_thresh)
    enable_trailing = bool(cfg.get("enable_trailing", False))
    trailing_enable_atr_mult = float(cfg.get("trailing_enable_atr_mult", 1.0))
    trailing_atr_mult = float(cfg.get("trailing_atr_mult", 0.5))
    long_only_above_slow = bool(cfg.get("long_only_above_slow", False))
    slope_lookback = int(cfg.get("slope_lookback", 0))
    cooldown = int(cfg.get("cooldown", 0))
    allow_short = bool(cfg.get("allow_short", True))
    short_only_below_slow = bool(cfg.get("short_only_below_slow", False))
    risk_per_trade_pct = cfg.get("risk_per_trade_pct")
    max_drawdown_pct = cfg.get("max_drawdown_pct")
    max_position_units = cfg.get("max_position_units")
    htf_factor = int(cfg.get("htf_factor", 4))
    htf_ema_window = cfg.get("htf_ema_window")
    if htf_ema_window is not None:
        htf_ema_window = int(htf_ema_window)
    htf_rsi_period = cfg.get("htf_rsi_period")
    if htf_rsi_period is not None:
        htf_rsi_period = int(htf_rsi_period)

    cfg_fx_rates = _coerce_fx_rates(cfg.get("fx_rates"))
    cli_fx_rates = _coerce_fx_rates(args.fx_rate)
    fx_rates = _merge_fx_rates(cfg_fx_rates, cli_fx_rates)

    strategy_specs = parse_strategy_specs(cfg.get("strategies"))

    engine = StrategyEngine(
        symbol=symbol,
        fast_win=fast_win,
        slow_win=slow_win,
        spread_pips=spread,
        commission_per_million=comm,
        slippage_pips=slip,
        stop_loss_pips=stop_loss_pips,
        take_profit_pips=take_profit_pips,
        atr_sl=atr_sl,
        atr_tp=atr_tp,
        atr_window=atr_window,
        regime_ema_window=regime_ema_window,
        regime_slope_min=regime_slope_min,
        regime_atr_min=regime_atr_min,
        rsi_period=rsi_period,
        rsi_long_thresh=rsi_long_thresh,
        rsi_short_thresh=rsi_short_thresh,
        enable_trailing=enable_trailing,
        trailing_enable_atr_mult=trailing_enable_atr_mult,
        trailing_atr_mult=trailing_atr_mult,
        long_only_above_slow=long_only_above_slow,
        slope_lookback=slope_lookback,
        cooldown=cooldown,
        qty=qty,
        account_ccy=account_ccy,
        fx_rates=fx_rates,
        strategy_specs=strategy_specs,
        allow_short=allow_short,
        short_only_below_slow=short_only_below_slow,
        risk_per_trade_pct=risk_per_trade_pct,
        max_drawdown_pct=max_drawdown_pct,
        max_position_units=max_position_units,
        htf_factor=htf_factor,
        htf_ema_window=htf_ema_window,
        htf_rsi_period=htf_rsi_period,
        execution_handler=execution_handler,
    )
    engine.set_initial_cash(initial_cash)
    return engine, symbol, initial_cash


def _format_labels(labels: dict) -> str:
    return ",".join(f'{k}="{v}"' for k, v in labels.items())


def push_metrics(push_url: str | None, job: str, metrics: dict, labels: dict) -> None:
    if not push_url or not metrics:
        return
    try:
        body = "\n".join(f"{k}{{{_format_labels(labels)}}} {v}" for k, v in metrics.items()) + "\n"
        url = push_url.rstrip("/") + f"/metrics/job/{job}"
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="PUT")
        urllib.request.urlopen(req, timeout=2)
    except Exception as exc:
        logger.warning("[Metrics] Pushgateway push failed: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="OANDA paper trading driver")
    parser.add_argument("--config", required=True, help="策略配置 YAML")
    parser.add_argument("--symbol", default=None, help="覆盖配置中的交易品种")
    parser.add_argument("--timeframe", default="60s", help="K线时间粒度，默认 60s")
    parser.add_argument("--environment", default="practice", choices=["practice", "live"], help="OANDA 环境")
    parser.add_argument("--fx-rate", action="append", default=None, help="额外汇率，示例 GBPUSD=1.27")
    parser.add_argument("--max-bars", type=int, default=None, help="最多生成多少根 bar 后自动停止")
    parser.add_argument("--log-heartbeat", action="store_true", help="打印 OANDA 心跳信息")
    parser.add_argument("--ledger-path", default=None, help="可选 ledger 路径（JSONL），用于重放本地账本并初始化状态")
    parser.add_argument(
        "--risk-profile",
        default=str(DEFAULT_RISK_PROFILE),
        help="风险配置 YAML（提供 risk_scale/max_leverage），默认 QuantTrader/config/risk_profile.yaml",
    )
    parser.add_argument(
        "--kill-check-interval",
        type=int,
        default=10,
        help="Kill-switch 检查频率（每处理多少根 bar 检查账户状态）",
    )
    parser.add_argument("--qty", type=float, default=None, help="覆盖配置中的下单名义（单位与配置一致）")
    parser.add_argument("--cash", type=float, default=None, help="覆盖配置中的初始资金")
    parser.add_argument("--warmup-csv", type=str, default=None, help="Warmup 历史 K 线 CSV 路径（如 backtest 用的 clean CSV）")
    parser.add_argument("--warmup-bars", type=int, default=200, help="Warmup 多少根历史 K 线（默认 200）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.qty is not None:
        cfg["qty"] = float(args.qty)
    if args.cash is not None:
        cfg["cash"] = float(args.cash)
    if args.warmup_csv is None and cfg.get("csv"):
        args.warmup_csv = cfg.get("csv")

    risk_profile = load_risk_profile(args.risk_profile)
    base_qty = float(cfg.get("qty", 10_000))
    scaled_qty = base_qty * float(risk_profile.risk_scale or 1.0)
    cfg["qty"] = scaled_qty
    if risk_profile.risk_scale != 1.0:
        logger.info(
            "[RISK] risk_scale=%.3f applied to %s (base %.0f -> scaled %.0f)",
            risk_profile.risk_scale,
            args.symbol or cfg.get("symbol", ""),
            base_qty,
            scaled_qty,
        )
    kill_switch = KillSwitch(
        nav_floor=risk_profile.nav_floor,
        margin_ratio_floor=risk_profile.margin_ratio_floor,
        leverage_ceiling=risk_profile.max_leverage,
    )
    push_url = os.getenv("PUSHGATEWAY_URL")
    metrics_job = "paper_trade"
    heartbeat_interval = 30.0
    last_heartbeat = time.time()

    token = OANDA_TOKEN
    account_id = OANDA_ACCOUNT_ID
    if not token or not account_id:
        raise RuntimeError("OANDA_TOKEN 或 OANDA_ACCOUNT_ID 未在环境变量中设置")

    order_queue: Queue = Queue()
    ledger_state = None
    if args.ledger_path:
        try:
            ledger = Ledger(args.ledger_path)
            ledger_state = ledger.replay()
            pos_summary = {k: (v.quantity, v.avg_price) for k, v in ledger_state.positions.items()}
            logger.info(
                "[Ledger] Replayed %s events -> cash=%.2f, positions=%s, open_orders=%d",
                len(ledger.events),
                ledger_state.cash,
                pos_summary,
                len(ledger_state.open_orders),
            )
        except Exception as exc:
            logger.warning(f"[Ledger] Failed to replay ledger {args.ledger_path}: {exc}")

    execution = OandaExecution(order_queue, account_id=account_id, access_token=token, environment=args.environment)
    engine, symbol, initial_cash = build_engine(cfg, args, execution.on_event)

    remote_state = fetch_remote_state(account_id, token, args.environment, symbol)

    if ledger_state:
        if ledger_state.cash:
            if initial_cash:
                diff = abs(ledger_state.cash - initial_cash) / initial_cash
                if diff > 0.05:
                    logger.warning(
                        "[Ledger] Cash %.2f differs from config cash %.2f by %.2f%%; using ledger value",
                        ledger_state.cash,
                        initial_cash,
                        diff * 100.0,
                    )
            engine.set_initial_cash(ledger_state.cash)
        pos = ledger_state.positions.get(symbol)
        if pos and (pos.quantity != 0):
            engine.sync_position_state(
                quantity=pos.quantity,
                avg_price=pos.avg_price,
                last_close=None,
                nav=ledger_state.cash or initial_cash,
                unrealized_pnl=None,
                order_book_state=[asdict(x) if hasattr(x, "__dict__") else dict(x) for x in ledger_state.open_orders.values()] if ledger_state.open_orders else None,
            )

    # Reconcile with remote broker state if available
    if remote_state:
        remote_pos = remote_state["positions"].get(symbol) if remote_state.get("positions") else None
        if remote_pos:
            engine.sync_position_state(
                quantity=remote_pos.get("quantity", 0.0),
                avg_price=remote_pos.get("avg_price"),
                last_close=None,
                nav=remote_state.get("nav") or engine.initial_cash,
                unrealized_pnl=remote_pos.get("unrealized_pnl"),
                order_book_state=remote_state.get("open_orders"),
            )
        elif remote_state.get("nav"):
            engine.set_initial_cash(remote_state["nav"])

    # Warmup: feed historical bars to stabilize indicators
    if args.warmup_csv:
        try:
            import pandas as pd

            warm_df = pd.read_csv(args.warmup_csv)
            if "time" in warm_df and {"open", "high", "low", "close"}.issubset(warm_df.columns):
                warm_df["ts"] = pd.to_datetime(warm_df["time"], utc=True)
                tail = warm_df.tail(args.warmup_bars)
                for _, row in tail.iterrows():
                    bar_ev = {
                        "type": "bar",
                        "symbol": symbol,
                        "ts": row["ts"],
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]) if "volume" in row else 0.0,
                    }
                    engine.handle_bar(bar_ev)
                logger.info("[Warmup] Loaded %d bars from %s", len(tail), args.warmup_csv)
            else:
                logger.warning("[Warmup] CSV %s missing required columns time/open/high/low/close; skipping", args.warmup_csv)
        except Exception as exc:
            logger.warning(f"[Warmup] Failed to warmup from {args.warmup_csv}: {exc}")

    aggregator = BarAggregator(symbol, args.timeframe)

    tick_queue: Queue = Queue()
    stream = OandaPricingStream(
        tick_queue,
        account_id=account_id,
        instruments=[symbol],
        access_token=token,
        environment=args.environment,
        log_heartbeat=args.log_heartbeat,
    )

    stop_flag = False

    def handle_sigterm(signum, frame):
        nonlocal stop_flag
        stop_flag = True

    signal.signal(signal.SIGINT, handle_sigterm)
    signal.signal(signal.SIGTERM, handle_sigterm)

    logger.info(f"[Paper] Starting pricing stream for {symbol} ({args.timeframe})")
    stream.start()

    bars_processed = 0
    kill_check_interval = max(1, int(args.kill_check_interval or 1))
    last_kill_check = 0

    try:
        while not stop_flag:
            try:
                tick = tick_queue.get(timeout=1.0)
            except Empty:
                now = time.time()
                if push_url and heartbeat_interval > 0 and now - last_heartbeat >= heartbeat_interval:
                    last_heartbeat = now
                    push_metrics(
                        push_url,
                        metrics_job,
                        {"fx_heartbeat": 1, "fx_heartbeat_ts": now},
                        {"symbol": symbol, "environment": args.environment},
                    )
                continue
            if not isinstance(tick, TickEvent):
                continue
            bar = aggregator.update(tick)
            if bar:
                engine.handle_bar(bar)
                bars_processed += 1

                if (
                    kill_switch.enabled
                    and bars_processed - last_kill_check >= kill_check_interval
                ):
                    last_kill_check = bars_processed
                    try:
                        snapshot = snapshot_account()
                    except Exception as exc:
                        logger.warning(f"[KILL SWITCH] Failed to fetch account snapshot: {exc}")
                    else:
                        reason = kill_switch.should_trigger(snapshot)
                        if reason:
                            kill_switch.log_trigger(reason)
                            push_metrics(
                                push_url,
                                metrics_job,
                                {"fx_kill_switch_tripped": 1},
                                {"symbol": symbol, "environment": args.environment, "reason": reason},
                            )
                            stop_flag = True
                            break

                if push_url and args.metrics_interval and bars_processed % args.metrics_interval == 0:
                    eq = engine._current_equity(bar["close"], bar["ts"])
                    pos_units = getattr(engine, "position_units", 0.0)
                    notional = pos_units * float(bar.get("close", 0.0))
                    push_metrics(
                        push_url,
                        metrics_job,
                        {
                            "fx_heartbeat": 1,
                            "fx_heartbeat_ts": time.time(),
                            "fx_equity": eq,
                            "fx_position_units": pos_units,
                            "fx_notional_est": notional,
                            "fx_trade_count": engine.trade_count,
                            "fx_bars_processed": bars_processed,
                        },
                        {"symbol": symbol, "environment": args.environment},
                    )

                if args.max_bars and bars_processed >= args.max_bars:
                    logger.info("[Paper] Reached max bar limit, stopping.")
                    break
    finally:
        stream.stop()

    # flush last partially built bar
    final_bar = aggregator.flush()
    if final_bar:
        engine.handle_bar(final_bar)

    engine.finalize()
    suffix = engine.compute_suffix()
    engine.export_outputs(
        fast_win=int(cfg.get("fast", 50)),
        slow_win=int(cfg.get("slow", 200)),
        suffix=suffix,
    )
    result = engine.summary(
        fast_win=int(cfg.get("fast", 50)),
        slow_win=int(cfg.get("slow", 200)),
        suffix=suffix,
    )
    final_equity = result["final_equity"] if result["final_equity"] is not None else engine.cash
    ret_pct = (final_equity / initial_cash - 1.0) * 100.0
    logger.info(f"[Paper] Bars processed: {engine.bar_count}, Trades executed: {engine.trade_count}")
    logger.info(f"[Paper] Final equity: {final_equity:.2f} ({ret_pct:.2f}%)")

    # Drain any fills left in queue
    fills = []
    while True:
        try:
            fill = order_queue.get_nowait()
        except Empty:
            break
        else:
            fills.append(fill)
    if fills:
        for fill in fills:
            logger.info(f"[Paper] Fill received: {fill}")


if __name__ == "__main__":
    main()
