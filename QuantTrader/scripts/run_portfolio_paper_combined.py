#!/usr/bin/env python3
"""Launch multiple paper_trade sleeves within a single OANDA pricing stream."""

from __future__ import annotations

import argparse
import json
import signal
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from types import SimpleNamespace
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

import os
import sys

TRADER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TRADER_ROOT.parent
RESEARCH_ROOT = REPO_ROOT / "QuantResearch"
for path in (TRADER_ROOT, REPO_ROOT, RESEARCH_ROOT):
    if str(path) not in sys.path:
        sys.path.append(str(path))

from data.oanda_stream import OandaPricingStream
from core.oanda_execution import OandaExecution
from core.events import TickEvent
from QuantResearch.core.backtest.ledger import Ledger
from shared.utils.config import OANDA_ACCOUNT_ID, OANDA_TOKEN
from shared.utils.oanda_client import snapshot_account
from utils.kill_switch import KillSwitch
from utils.risk import DEFAULT_RISK_PROFILE, load_risk_profile

from scripts.paper_trade import (
    BarAggregator,
    build_engine,
    fetch_remote_state,
    load_config,
)


def parse_kv_pairs(values: Optional[List[str]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not values:
        return mapping
    for item in values:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"Invalid pair '{item}'. Expect format SYMBOL=PATH"
            )
        key, value = item.split("=", 1)
        norm = key.strip().upper().replace("_", "")
        if not norm:
            raise argparse.ArgumentTypeError(f"Invalid symbol in '{item}'")
        mapping[norm] = value.strip()
    return mapping


def load_weights(path: Path) -> Dict[str, float]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: float(v) for k, v in data.items()}


def compute_qty(cfg: Dict, weight: float, total_cash: float, max_qty_mult: float, min_qty: float) -> float:
    base_cash = float(cfg.get("cash", 100000.0))
    base_qty = float(cfg.get("qty", 10000.0))
    if base_cash <= 0:
        scale = weight
    else:
        scale = (total_cash * weight) / base_cash
    qty = base_qty * scale
    qty = min(qty, base_qty * max_qty_mult)
    qty = max(qty, min_qty)
    return qty


@dataclass
class SleeveContext:
    name: str
    symbol: str
    cfg_path: Path
    cfg: Dict
    engine: any
    aggregator: BarAggregator
    execution: OandaExecution
    order_queue: Queue
    initial_cash: float
    ledger_state: Optional[any] = None
    bars_processed: int = 0

    def handle_bar(self, bar: dict) -> None:
        self.engine.handle_bar(bar)
        self.bars_processed += 1

    def finalize(self) -> None:
        self.engine.finalize()
        suffix = self.engine.compute_suffix()
        self.engine.export_outputs(
            fast_win=int(self.cfg.get("fast", 50)),
            slow_win=int(self.cfg.get("slow", 200)),
            suffix=suffix,
        )
        result = self.engine.summary(
            fast_win=int(self.cfg.get("fast", 50)),
            slow_win=int(self.cfg.get("slow", 200)),
            suffix=suffix,
        )
        final_equity = (
            result.get("final_equity")
            if result and result.get("final_equity") is not None
            else self.engine.cash
        )
        ret_pct = 0.0
        if self.initial_cash:
            ret_pct = (final_equity / self.initial_cash - 1.0) * 100.0
        logger.info(
            "[Paper][%s] Bars processed=%d, Trades=%d, Final equity=%.2f (%.2f%%)",
            self.symbol,
            self.engine.bar_count,
            self.engine.trade_count,
            final_equity,
            ret_pct,
        )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Run multiple paper_trade configs in a single pricing stream."
    )
    ap.add_argument(
        "--configs",
        nargs="+",
        required=True,
        help="List of strategy YAML configs.",
    )
    ap.add_argument(
        "--weight-file",
        required=True,
        help="JSON mapping config filename -> weight.",
    )
    ap.add_argument(
        "--total-cash",
        type=float,
        default=None,
        help="Total capital; defaults to OANDA NAV snapshot.",
    )
    ap.add_argument(
        "--environment",
        default="practice",
        choices=["practice", "live"],
        help="OANDA environment.",
    )
    ap.add_argument("--timeframe", default="60s", help="Bar timeframe. (default 60s)")
    ap.add_argument(
        "--max-qty-mult",
        type=float,
        default=3.0,
        help="Cap qty to base_qty * max_qty_mult",
    )
    ap.add_argument(
        "--min-qty",
        type=float,
        default=0.0,
        help="Minimum qty after scaling",
    )
    ap.add_argument(
        "--risk-profile",
        default=str(DEFAULT_RISK_PROFILE),
        help="Risk profile YAML for risk_scale/max_leverage",
    )
    ap.add_argument(
        "--kill-check-interval",
        type=int,
        default=10,
        help="Check kill-switch every N bars (aggregated across sleeves).",
    )
    ap.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Optional cap on bars per sleeve before stopping.",
    )
    ap.add_argument(
        "--log-heartbeat",
        action="store_true",
        help="Enable OANDA heartbeat logging.",
    )
    ap.add_argument(
        "--reconnect-wait",
        type=float,
        default=5.0,
        help="Seconds to wait before reconnecting pricing stream.",
    )
    ap.add_argument(
        "--warmup-bars",
        type=int,
        default=200,
        help="Warmup bars from CSV before live ticks.",
    )
    ap.add_argument(
        "--fx-rate",
        action="append",
        default=None,
        help="Optional FX overrides, e.g. GBPUSD=1.27",
    )
    ap.add_argument(
        "--ledger",
        action="append",
        default=None,
        help="Optional per-symbol ledger mapping (SYMBOL=path).",
    )
    return ap.parse_args()


def resolve_total_cash(args: argparse.Namespace) -> float:
    if args.total_cash is not None:
        return float(args.total_cash)
    snapshot = snapshot_account()
    nav = snapshot.get("nav")
    if nav in (None, 0, ""):
        raise RuntimeError(
            "snapshot_account returned empty NAV. Provide --total-cash explicitly."
        )
    logger.info("[NAV] Using account NAV=%.2f from snapshot", float(nav))
    return float(nav)


def warmup_engine(cfg: Dict, args: argparse.Namespace, context: SleeveContext) -> None:
    warmup_csv = cfg.get("csv")
    if not warmup_csv:
        return
    try:
        warm_df = pd.read_csv(warmup_csv)
        if "time" in warm_df and {"open", "high", "low", "close"}.issubset(warm_df.columns):
            warm_df["ts"] = pd.to_datetime(warm_df["time"], utc=True)
            tail = warm_df.tail(args.warmup_bars)
            for _, row in tail.iterrows():
                bar_ev = {
                    "type": "bar",
                    "symbol": context.symbol,
                    "ts": row["ts"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]) if "volume" in row else 0.0,
                }
                context.engine.handle_bar(bar_ev)
            logger.info(
                "[Warmup][%s] Loaded %d bars from %s",
                context.symbol,
                len(tail),
                warmup_csv,
            )
        else:
            logger.warning(
                "[Warmup][%s] CSV %s missing columns time/open/high/low/close; skipping",
                context.symbol,
                warmup_csv,
            )
    except Exception as exc:
        logger.warning(
            "[Warmup][%s] Failed to warmup from %s: %s",
            context.symbol,
            warmup_csv,
            exc,
        )


def apply_ledger_and_remote(
    ctx: SleeveContext,
    ledger_path: Optional[str],
    account_id: str,
    token: str,
    environment: str,
):
    ledger_state = None
    if ledger_path:
        try:
            ledger = Ledger(ledger_path)
            ledger_state = ledger.replay()
            ctx.engine.set_initial_cash(ledger_state.cash)
            pos = ledger_state.positions.get(ctx.symbol)
            if pos and pos.quantity:
                ctx.engine.sync_position_state(
                    quantity=pos.quantity,
                    avg_price=pos.avg_price,
                    last_close=None,
                    nav=ledger_state.cash,
                    unrealized_pnl=None,
                    order_book_state=None,
                )
            logger.info(
                "[Ledger][%s] Replayed %s events",
                ctx.symbol,
                len(ledger.events),
            )
        except Exception as exc:
            logger.warning(
                "[Ledger][%s] Failed to replay %s: %s",
                ctx.symbol,
                ledger_path,
                exc,
            )
    ctx.ledger_state = ledger_state

    remote_state = fetch_remote_state(account_id, token, environment, ctx.symbol)
    if remote_state:
        remote_pos = remote_state["positions"].get(ctx.symbol) if remote_state.get("positions") else None
        if remote_pos:
            ctx.engine.sync_position_state(
                quantity=remote_pos.get("quantity", 0.0),
                avg_price=remote_pos.get("avg_price"),
                last_close=None,
                nav=remote_state.get("nav") or ctx.engine.initial_cash,
                unrealized_pnl=remote_pos.get("unrealized_pnl"),
                order_book_state=remote_state.get("open_orders"),
            )
        elif remote_state.get("nav"):
            ctx.engine.set_initial_cash(remote_state["nav"])


def main() -> None:
    args = parse_args()
    weights = load_weights(Path(args.weight_file))
    ledger_map = parse_kv_pairs(args.ledger)

    token = OANDA_TOKEN
    account_id = OANDA_ACCOUNT_ID
    if not token or not account_id:
        raise RuntimeError("OANDA_TOKEN or OANDA_ACCOUNT_ID not configured")

    risk_profile = load_risk_profile(args.risk_profile)
    kill_switch = KillSwitch(
        nav_floor=risk_profile.nav_floor,
        margin_ratio_floor=risk_profile.margin_ratio_floor,
        leverage_ceiling=risk_profile.max_leverage,
    )

    total_cash = resolve_total_cash(args)

    sleeves: List[SleeveContext] = []
    symbols: List[str] = []

    for cfg_path_str in args.configs:
        cfg_path = Path(cfg_path_str).resolve()
        cfg_name = cfg_path.name
        if cfg_name not in weights:
            raise RuntimeError(f"{cfg_name} missing in weight file {args.weight_file}")
        weight = weights[cfg_name]
        cfg = load_config(str(cfg_path)) or {}
        qty = compute_qty(cfg, weight, total_cash, args.max_qty_mult, args.min_qty)
        if qty <= 0:
            logger.info("[SKIP] %s weight=%.4f qty=%.2f", cfg_name, weight, qty)
            continue
        cfg["qty"] = qty
        base_qty = float(cfg.get("qty", 10_000))
        scaled_qty = base_qty * float(risk_profile.risk_scale or 1.0)
        cfg["qty"] = scaled_qty
        symbol = cfg.get("symbol", "EURUSD").upper().replace("/", "").replace("_", "")
        if risk_profile.risk_scale != 1.0:
            logger.info(
                "[RISK][%s] risk_scale=%.3f applied (base %.0f -> scaled %.0f)",
                symbol,
                risk_profile.risk_scale,
                base_qty,
                scaled_qty,
            )

        order_queue: Queue = Queue()
        execution = OandaExecution(
            order_queue,
            account_id=account_id,
            access_token=token,
            environment=args.environment,
        )
        pseudo_args = SimpleNamespace(symbol=None, fx_rate=args.fx_rate)
        engine, resolved_symbol, initial_cash = build_engine(cfg, pseudo_args, execution.on_event)
        resolved_symbol = resolved_symbol.replace("_", "")
        aggregator = BarAggregator(resolved_symbol, args.timeframe)

        ctx = SleeveContext(
            name=cfg_name,
            symbol=resolved_symbol,
            cfg_path=cfg_path,
            cfg=cfg,
            engine=engine,
            aggregator=aggregator,
            execution=execution,
            order_queue=order_queue,
            initial_cash=initial_cash,
        )
        warmup_engine(cfg, args, ctx)
        apply_ledger_and_remote(
            ctx,
            ledger_map.get(resolved_symbol),
            account_id,
            token,
            args.environment,
        )
        sleeves.append(ctx)
        symbols.append(resolved_symbol)
        logger.info(
            "[PLAN] %s weight=%.4f qty=%.0f symbol=%s",
            cfg_name,
            weight,
            scaled_qty,
            resolved_symbol,
        )

    if not sleeves:
        logger.warning("No sleeves to run. Exiting.")
        return

    tick_queue: Queue = Queue()
    stream = OandaPricingStream(
        tick_queue,
        account_id=account_id,
        instruments=symbols,
        access_token=token,
        environment=args.environment,
        reconnect_wait=args.reconnect_wait,
        log_heartbeat=args.log_heartbeat,
    )

    stop_flag = False
    symbol_map = {ctx.symbol: ctx for ctx in sleeves}

    def handle_stop(signum, frame):
        nonlocal stop_flag
        stop_flag = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    logger.info("[Paper][Combined] Starting pricing stream for %s", ",".join(symbols))
    stream.start()

    total_bars = 0
    bars_since_kill = 0

    try:
        while not stop_flag:
            try:
                tick = tick_queue.get(timeout=1.0)
            except Empty:
                continue
            if not isinstance(tick, TickEvent):
                continue
            sym = tick.symbol.upper()
            ctx = symbol_map.get(sym)
            if ctx is None:
                continue
            bar = ctx.aggregator.update(tick)
            if bar:
                ctx.handle_bar(bar)
                total_bars += 1
                bars_since_kill += 1
                if args.max_bars and ctx.bars_processed >= args.max_bars:
                    logger.info(
                        "[Paper][%s] Reached max bars %d; stopping.",
                        ctx.symbol,
                        args.max_bars,
                    )
                    stop_flag = True
                    break
                if kill_switch.enabled and bars_since_kill >= args.kill_check_interval:
                    bars_since_kill = 0
                    try:
                        snapshot = snapshot_account()
                    except Exception as exc:
                        logger.warning("[KILL SWITCH] Failed to fetch snapshot: %s", exc)
                    else:
                        reason = kill_switch.should_trigger(snapshot)
                        if reason:
                            kill_switch.log_trigger(reason)
                            stop_flag = True
                            break
    finally:
        stream.stop()
        for ctx in sleeves:
            final_bar = ctx.aggregator.flush()
            if final_bar:
                ctx.handle_bar(final_bar)
            ctx.finalize()


if __name__ == "__main__":
    main()
