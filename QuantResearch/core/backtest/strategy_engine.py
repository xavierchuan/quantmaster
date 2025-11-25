"""
Shared strategy engine for backtesting and paper/live trading.

This module consolidates the logic that used to live inside scripts/backtest_strategy.py
so that backtest, paper trading, and other tooling can all reuse one implementation.
"""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Tuple, Union

import numpy as np
import pandas as pd
from loguru import logger

from core.events import OrderEvent
from metrics.perf import compute_metrics, trade_stats


FXRateProvider = Optional[Union[Mapping[Tuple[str, str], float], Callable[..., Optional[float]]]]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = DATA_DIR / "outputs"
EQUITY_DIR = OUTPUT_DIR / "equity"
TRADES_DIR = OUTPUT_DIR / "trades"
STATS_DIR = OUTPUT_DIR / "stats"


def _norm_ccy(ccy: str) -> str:
    return ccy.upper() if ccy else ccy


def _split_pair_key(pair: str) -> Tuple[str, str]:
    cleaned = pair.replace("-", "").replace("_", "").replace("/", "").replace(" ", "").upper()
    if len(cleaned) != 6:
        raise ValueError(f"Invalid FX pair key: {pair}")
    return cleaned[:3], cleaned[3:]


def _lookup_fx_rate(
    from_ccy: str,
    to_ccy: str,
    fx_rates: FXRateProvider,
    timestamp: Optional[object] = None,
) -> Optional[float]:
    if fx_rates is None:
        return None
    f = _norm_ccy(from_ccy)
    t = _norm_ccy(to_ccy)
    if f == t:
        return 1.0
    if callable(fx_rates):
        try:
            return fx_rates(f, t, timestamp)
        except TypeError:
            return fx_rates(f, t)
    rate = fx_rates.get((f, t))
    if rate is None:
        inv = fx_rates.get((t, f))
        if inv not in (None, 0):
            rate = 1.0 / inv
    return rate


def convert_currency(
    amount: float,
    from_ccy: str,
    to_ccy: str,
    fx_rates: FXRateProvider,
    timestamp: Optional[object] = None,
) -> float:
    f = _norm_ccy(from_ccy)
    t = _norm_ccy(to_ccy)
    if f == t:
        return amount
    rate = _lookup_fx_rate(f, t, fx_rates, timestamp)
    if rate in (None, 0):
        logger.debug(f"No FX rate for {f}/{t}; using original amount.")
        return amount
    return amount * rate


def pnl_to_account(
    base: str,
    quote: str,
    entry_price: float,
    exit_price: float,
    units: float,
    account_ccy: str = "USD",
    fx_rates: FXRateProvider = None,
    timestamp: Optional[object] = None,
    side: str = "LONG",
) -> float:
    base_n = _norm_ccy(base)
    quote_n = _norm_ccy(quote)
    acct = _norm_ccy(account_ccy)
    direction = 1.0 if side.upper() != "SHORT" else -1.0
    raw = (exit_price - entry_price) * units * direction
    if quote_n == acct:
        return raw
    if base_n == acct:
        return raw / exit_price if exit_price != 0 else 0.0
    return convert_currency(raw, quote_n, acct, fx_rates, timestamp)


def notional_in_account(
    price: float,
    units: float,
    base: str,
    quote: str,
    account_ccy: str = "USD",
    fx_rates: FXRateProvider = None,
    timestamp: Optional[object] = None,
) -> float:
    base_n = _norm_ccy(base)
    quote_n = _norm_ccy(quote)
    acct = _norm_ccy(account_ccy)
    if base_n == acct:
        return units
    notional_quote = price * units
    if quote_n == acct:
        return notional_quote
    return convert_currency(notional_quote, quote_n, acct, fx_rates, timestamp)


def _parse_fx_rate_entry(entry: str) -> Optional[Tuple[Tuple[str, str], float]]:
    if "=" not in entry:
        return None
    key, value = entry.split("=", 1)
    try:
        pair = _split_pair_key(key)
        rate = float(value)
    except ValueError:
        logger.warning(f"忽略无法解析的 FX 汇率条目: {entry}")
        return None
    return pair, rate


def _coerce_fx_rates(data) -> FXRateProvider:
    if data is None:
        return None
    if callable(data):
        return data
    result: dict[Tuple[str, str], float] = {}
    if isinstance(data, Mapping):
        for k, v in data.items():
            try:
                if isinstance(v, Mapping) and len(str(k).strip()) == 3:
                    base = _norm_ccy(str(k))
                    for inner_k, inner_v in v.items():
                        quote = _norm_ccy(str(inner_k))
                        result[(base, quote)] = float(inner_v)
                else:
                    pair = _split_pair_key(str(k))
                    result[pair] = float(v)
            except ValueError:
                logger.warning(f"忽略无法解析的 FX 汇率键值对: {k} -> {v}")
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            if isinstance(item, Mapping):
                sub = _coerce_fx_rates(item)
                if isinstance(sub, Mapping):
                    result.update(sub)
            else:
                parsed = _parse_fx_rate_entry(str(item))
                if parsed:
                    result[parsed[0]] = parsed[1]
    elif isinstance(data, str):
        parsed = _parse_fx_rate_entry(data)
        if parsed:
            result[parsed[0]] = parsed[1]
    else:
        logger.warning(f"无法解析的 fx_rates 类型: {type(data)!r}")
    return result or None


def _merge_fx_rates(base: FXRateProvider, override: FXRateProvider) -> FXRateProvider:
    if override is None:
        return base
    if callable(override):
        return override
    if callable(base):
        logger.warning("fx_rates: 命令行配置覆盖了可调用的基础配置。")
        return override
    merged: dict[Tuple[str, str], float] = {}
    if isinstance(base, Mapping):
        merged.update(base)
    if isinstance(override, Mapping):
        merged.update(override)
    return merged or None


@dataclass
class StrategySpec:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0


def parse_strategy_specs(data: Any) -> Optional[List[StrategySpec]]:
    if not data:
        return None
    specs: List[StrategySpec] = []
    iterable = data if isinstance(data, (list, tuple)) else [data]
    for item in iterable:
        if isinstance(item, StrategySpec):
            specs.append(item)
        elif isinstance(item, Mapping):
            name = item.get("name")
            if not name:
                continue
            params = item.get("params")
            if params is None:
                params = {k: v for k, v in item.items() if k not in ("name", "weight")}
            if not isinstance(params, Mapping):
                params = {}
            weight = float(item.get("weight", 1.0))
            specs.append(StrategySpec(str(name), dict(params), weight=weight))
    return specs or None


class StrategyEngine:
    """
    Core loop that processes bar data and emits orders/fills according to strategies.
    """

    def __init__(
        self,
        symbol: str,
        fast_win: int,
        slow_win: int,
        spread_pips: float,
        commission_per_million: float,
        slippage_pips: float,
        stop_loss_pips: Optional[float],
        take_profit_pips: Optional[float],
        atr_sl: Optional[float],
        atr_tp: Optional[float],
        atr_window: int,
        long_only_above_slow: bool,
        slope_lookback: int,
        cooldown: int,
        qty: float,
        account_ccy: str,
        fx_rates: FXRateProvider,
        strategy_specs: Optional[List[StrategySpec]] = None,
        cost_profiles: Optional[Any] = None,
        slippage_model: Optional[Mapping[str, Any]] = None,
        strategy_combine_mode: str = "first_hit",
        strategy_vote_threshold: float = 0.0,
        allow_short: bool = True,
        short_only_below_slow: bool = False,
        risk_per_trade_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        max_position_units: Optional[float] = None,
        regime_ema_window: int = 200,
        regime_slope_min: Optional[float] = None,
        regime_atr_min: Optional[float] = None,
        regime_atr_percentile_min: Optional[float] = None,
        regime_atr_percentile_window: int = 500,
        regime_trend_min_bars: int = 0,
        rsi_period: int = 14,
        rsi_long_thresh: Optional[float] = None,
        rsi_short_thresh: Optional[float] = None,
        enable_trailing: bool = False,
        trailing_enable_atr_mult: float = 1.0,
        trailing_atr_mult: float = 0.5,
        skip_outlier_bars: bool = False,
        htf_factor: int = 4,
        htf_ema_window: Optional[int] = None,
        htf_rsi_period: Optional[int] = None,
        output_dirs: Optional[Mapping[str, Union[str, os.PathLike[str]]]] = None,
        execution_handler: Optional[Callable[[OrderEvent], None]] = None,
        stress_cost_spread_mult: float = 1.0,
        stress_cost_comm_mult: float = 1.0,
        stress_slippage_mult: float = 1.0,
        stress_price_vol_mult: float = 1.0,
        stress_skip_trade_pct: float = 0.0,
        atr_sl_map: Optional[Mapping[str, float]] = None,
        atr_tp_map: Optional[Mapping[str, float]] = None,
    ) -> None:
        from strategies import load_strategy  # lazy import to avoid circulars

        self.symbol = symbol
        self.base_ccy, self.quote_ccy = symbol[:3], symbol[3:]
        self.account_ccy = account_ccy.upper()
        self.fx_rates = fx_rates
        self.fast_win = int(fast_win)
        self.slow_win = int(slow_win)
        self.atr_window = int(atr_window)
        self.stop_loss_pips = stop_loss_pips
        self.take_profit_pips = take_profit_pips
        self.atr_sl = atr_sl
        self.atr_tp = atr_tp
        # Optional regime-aware overrides (multipliers applied to base ATR SL/TP)
        self.atr_sl_map = dict(atr_sl_map) if atr_sl_map else {}
        self.atr_tp_map = dict(atr_tp_map) if atr_tp_map else {}
        self.regime_ema_window = int(regime_ema_window) if regime_ema_window else 0
        self.regime_slope_min = regime_slope_min
        self.regime_atr_min = regime_atr_min
        self.regime_atr_percentile_min = regime_atr_percentile_min
        self.regime_atr_percentile_window = int(regime_atr_percentile_window or 0)
        self.regime_trend_min_bars = int(regime_trend_min_bars or 0)
        self.rsi_period = int(rsi_period) if rsi_period else 0
        self.rsi_long_thresh = rsi_long_thresh
        self.rsi_short_thresh = rsi_short_thresh
        self.enable_trailing = bool(enable_trailing)
        self.trailing_enable_atr_mult = float(trailing_enable_atr_mult)
        self.trailing_atr_mult = float(trailing_atr_mult)
        self.skip_outlier_bars = bool(skip_outlier_bars)
        self.htf_factor = max(1, int(htf_factor or 1))
        self.htf_ema_window = int(htf_ema_window) if htf_ema_window else None
        self.htf_rsi_period = int(htf_rsi_period) if htf_rsi_period else None
        self.htf_alpha = (2.0 / (self.htf_ema_window + 1)) if self.htf_ema_window else None
        self.long_only_above_slow = long_only_above_slow
        self.short_only_below_slow = short_only_below_slow
        self.slope_lookback = slope_lookback
        self.cooldown = cooldown
        self.allow_short = allow_short
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.max_position_units = max_position_units
        self.output_dirs = {
            "equity": str(EQUITY_DIR),
            "trades": str(TRADES_DIR),
            "stats": str(STATS_DIR),
        }
        if output_dirs:
            for key, value in output_dirs.items():
                if key in self.output_dirs and value is not None:
                    self.output_dirs[key] = str(value)
        self.execution_handler = execution_handler
        self.stress_cost_spread_mult = float(stress_cost_spread_mult or 1.0)
        self.stress_cost_comm_mult = float(stress_cost_comm_mult or 1.0)
        self.stress_slippage_mult = float(stress_slippage_mult or 1.0)
        self.stress_price_vol_mult = float(stress_price_vol_mult or 1.0)
        self.stress_skip_trade_pct = max(0.0, min(float(stress_skip_trade_pct or 0.0), 1.0))

        self.default_qty = float(qty)
        self.pip = 0.01 if self.quote_ccy.upper().endswith("JPY") else 0.0001

        self.base_spread_pips = float(spread_pips)
        self.base_slippage_pips = float(slippage_pips)
        self.base_commission_per_million = float(commission_per_million)
        self.cost_profiles, self.default_cost_profile = self._normalize_cost_profiles(
            cost_profiles,
            self.base_spread_pips,
            self.base_slippage_pips,
            self.base_commission_per_million,
        )
        self.slippage_model = slippage_model or {}
        self.active_cost_profile_name: Optional[str] = None
        self.profile_slip_pips = self.base_slippage_pips

        self.spread_pips = self.base_spread_pips
        self.slippage_pips = self.base_slippage_pips
        self.commission_per_million = self.base_commission_per_million
        self.half_spread = self.spread_pips * self.pip / 2.0
        self.slip = self.slippage_pips * self.pip
        # 初始化持仓/ATR，以便成本模块引用
        self.position = 0  # 1=long, -1=short
        self.position_units = 0.0
        self.entry_price: Optional[float] = None
        self.entry_side: Optional[str] = None
        self.entry_atr: Optional[float] = None
        self.stop_price: Optional[float] = None
        self.tp_price: Optional[float] = None
        self.curr_atr = None
        # 初始化为默认成本配置
        self._apply_cost_profile(self.default_cost_profile)
        self.active_cost_profile_name = self.default_cost_profile["name"]

        self.q_close_fast = deque(maxlen=self.fast_win)
        self.q_close_slow = deque(maxlen=self.slow_win)
        self.sma_fast_hist = deque(maxlen=self.fast_win + max(self.slope_lookback, 1) + 5)
        self.tr_win = deque(maxlen=self.atr_window)
        self.q_close_rsi = deque(maxlen=self.rsi_period) if self.rsi_period and self.rsi_period > 1 else None
        self.regime_alpha = (2.0 / (self.regime_ema_window + 1)) if self.regime_ema_window else None
        self.regime_ema = None
        self.prev_regime_ema = None
        self.regime_slope = None
        self.regime_label = "unknown"
        self.regime_trend_bars = 0
        self.atr_history = deque(maxlen=self.regime_atr_percentile_window) if self.regime_atr_percentile_window else None
        self.curr_atr_percentile: Optional[float] = None
        self.htf_buffer: List[float] = []
        self.htf_ema: Optional[float] = None
        self.htf_rsi_queue = deque(maxlen=self.htf_rsi_period) if self.htf_rsi_period and self.htf_rsi_period > 1 else None
        self.htf_rsi: Optional[float] = None

        # 持仓信息已在成本初始化前设定
        self.last_rsi: Optional[float] = None

        self.bar_idx = 0
        self.bar_count = 0
        self.next_entry_long = 0
        self.next_entry_short = 0

        self.cash = float(qty) * 0.0
        self.initial_cash = None
        self.pnl_realized = 0.0
        self.last_close = None
        self.prev_close = None

        self.equity_series: List[Tuple[object, float]] = []
        self.trade_log: List[dict[str, Any]] = []
        self.trade_count = 0
        self.trade_halted = False
        self.peak_equity = None
        self.manual_block_until = 0
        self.synced_order_book: list[dict[str, Any]] = []
        self.unrealized_hint: Optional[float] = None

        specs = strategy_specs or [StrategySpec("sma_atr", {
            "fast_win": self.fast_win,
            "slow_win": self.slow_win,
            "long_only_above_slow": self.long_only_above_slow,
            "slope_lookback": self.slope_lookback,
            "cooldown": self.cooldown,
            "atr_sl": self.atr_sl, "atr_tp": self.atr_tp, "atr_window": self.atr_window,
            "allow_short": self.allow_short,
            "short_only_below_slow": self.short_only_below_slow,
            "rsi_period": self.rsi_period,
            "rsi_long_thresh": self.rsi_long_thresh,
            "rsi_short_thresh": self.rsi_short_thresh,
        }, weight=1.0)]
        combine_mode = (strategy_combine_mode or "first_hit").lower()
        if combine_mode not in ("first_hit", "weighted"):
            raise ValueError(f"Unsupported strategy_combine_mode: {strategy_combine_mode}")
        self.strategy_combine_mode = combine_mode
        self.strategy_vote_threshold = float(strategy_vote_threshold or 0.0)
        self.strategy_meta = []
        for spec in specs:
            instance = load_strategy(spec.name, **spec.params)
            self.strategy_meta.append({
                "instance": instance,
                "weight": float(getattr(spec, "weight", 1.0)),
                "name": spec.name,
            })
        self.strategy_instances = [meta["instance"] for meta in self.strategy_meta]
        self.strategy_specs = specs

    def set_initial_cash(self, cash: float) -> None:
        self.cash = float(cash)
        self.initial_cash = float(cash)
        self.peak_equity = float(cash)

    def sync_position_state(
        self,
        quantity: float,
        avg_price: Optional[float] = None,
        last_close: Optional[float] = None,
        nav: Optional[float] = None,
        unrealized_pnl: Optional[float] = None,
        order_book_state: Optional[List[Mapping[str, Any]]] = None,
    ) -> None:
        """Force-set current position from an external source (e.g., ledger or broker).

        This is used during recovery/reconciliation to align the engine state with
        an existing account position before resuming bar processing.
        """
        qty = float(quantity or 0.0)
        self.position_units = abs(qty)
        if qty > 0:
            self.position = 1
            self.entry_side = "BUY"
        elif qty < 0:
            self.position = -1
            self.entry_side = "SELL"
        else:
            self.position = 0
            self.entry_side = None
        self.entry_price = float(avg_price) if avg_price is not None else self.entry_price
        if last_close is not None:
            self.last_close = float(last_close)
        if nav is not None:
            self.set_initial_cash(nav)
        if unrealized_pnl is not None:
            self.unrealized_hint = float(unrealized_pnl)
        if order_book_state is not None:
            # Store for downstream reconciliation/decision (e.g., whether to re-submit or cancel)
            self.synced_order_book = list(order_book_state)

    def _calc_commission(self, notional_account: float) -> float:
        return (notional_account / 1_000_000.0) * self.commission_per_million

    def _update_atr(self, high: float, low: float, close: float) -> None:
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        self.tr_win.append(tr)
        if len(self.tr_win) == self.atr_window:
            self.curr_atr = float(np.mean(self.tr_win))
            self._update_atr_percentile()
        self.prev_close = close
        self._update_htf(close)

    def _update_atr_percentile(self) -> None:
        if self.curr_atr is None or self.atr_history is None:
            self.curr_atr_percentile = None
            return
        self.atr_history.append(self.curr_atr)
        if len(self.atr_history) < max(10, int(self.regime_atr_percentile_window * 0.1) or 10):
            self.curr_atr_percentile = None
            return
        arr = np.fromiter(self.atr_history, dtype=float)
        if arr.size == 0:
            self.curr_atr_percentile = None
            return
        rank = float(np.sum(arr <= self.curr_atr))
        self.curr_atr_percentile = rank / float(arr.size)

    def _update_htf(self, close: float) -> None:
        if self.htf_factor <= 1 and not self.htf_alpha and not self.htf_rsi_queue:
            return
        self.htf_buffer.append(close)
        if len(self.htf_buffer) < self.htf_factor:
            return
        agg_close = float(sum(self.htf_buffer) / len(self.htf_buffer))
        self.htf_buffer.clear()
        if self.htf_alpha is not None:
            if self.htf_ema is None:
                self.htf_ema = agg_close
            else:
                self.htf_ema = self.htf_alpha * agg_close + (1.0 - self.htf_alpha) * self.htf_ema
        if self.htf_rsi_queue is not None:
            self.htf_rsi_queue.append(agg_close)
            if len(self.htf_rsi_queue) == self.htf_rsi_queue.maxlen:
                arr = np.array(self.htf_rsi_queue, dtype=float)
                diffs = np.diff(arr)
                gains = diffs[diffs > 0]
                losses = -diffs[diffs < 0]
                avg_gain = gains.mean() if gains.size > 0 else 0.0
                avg_loss = losses.mean() if losses.size > 0 else 0.0
                if avg_loss == 0.0 and avg_gain == 0.0:
                    self.htf_rsi = 50.0
                elif avg_loss == 0.0:
                    self.htf_rsi = 100.0
                else:
                    rs = avg_gain / avg_loss
                    self.htf_rsi = 100.0 - (100.0 / (1.0 + rs))

    def _compute_rsi_from_deque(self) -> Optional[float]:
        if not self.q_close_rsi or self.rsi_period <= 1:
            return None
        if len(self.q_close_rsi) < self.rsi_period:
            return None
        arr = np.array(self.q_close_rsi, dtype=float)
        diffs = np.diff(arr)
        gains = diffs[diffs > 0]
        losses = -diffs[diffs < 0]
        avg_gain = gains.mean() if gains.size > 0 else 0.0
        avg_loss = losses.mean() if losses.size > 0 else 0.0
        if avg_loss == 0.0 and avg_gain == 0.0:
            return 50.0
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return float(rsi)

    def _update_trailing_stop(self, close: float) -> None:
        if not self.enable_trailing or self.position == 0 or self.entry_price is None:
            return
        if self.entry_atr is None or self.curr_atr is None:
            return
        try:
            if self.position == 1:
                profit = close - self.entry_price
                threshold = self.trailing_enable_atr_mult * (self.entry_atr or 0.0)
                if profit >= threshold:
                    proposed = close - (self.trailing_atr_mult * self.curr_atr)
                    if self.stop_price is None or proposed > self.stop_price:
                        self.stop_price = proposed
            elif self.position == -1:
                profit = self.entry_price - close
                threshold = self.trailing_enable_atr_mult * (self.entry_atr or 0.0)
                if profit >= threshold:
                    proposed = close + (self.trailing_atr_mult * self.curr_atr)
                    if self.stop_price is None or proposed < self.stop_price:
                        self.stop_price = proposed
        except Exception:
            return

    def _update_regime_ema(self, close: float) -> None:
        if self.regime_alpha is None:
            return
        if self.regime_ema is None:
            self.regime_ema = close
            self.prev_regime_ema = close
            self.regime_slope = 0.0
            return
        self.prev_regime_ema = self.regime_ema
        self.regime_ema = self.regime_alpha * close + (1.0 - self.regime_alpha) * self.regime_ema
        self.regime_slope = (self.regime_ema - self.prev_regime_ema) if self.prev_regime_ema is not None else 0.0

    def _current_regime_label(self) -> str:
        if self.regime_alpha is None or self.regime_ema is None or self.prev_regime_ema is None:
            return "unknown"
        slope_abs = abs(self.regime_slope or 0.0)
        slope_ok = True if self.regime_slope_min is None else slope_abs >= abs(self.regime_slope_min)
        atr_ok = True
        if self.regime_atr_min is not None:
            atr_ok = self.curr_atr is not None and self.curr_atr >= self.regime_atr_min
        trend_like = slope_ok and atr_ok
        if trend_like and self.regime_atr_percentile_min is not None:
            if self.curr_atr_percentile is None:
                trend_like = False
            else:
                trend_like = self.curr_atr_percentile >= self.regime_atr_percentile_min

        if trend_like:
            self.regime_trend_bars = self.regime_trend_bars + 1
        else:
            self.regime_trend_bars = 0

        if trend_like and self.regime_trend_min_bars:
            trend_like = self.regime_trend_bars >= self.regime_trend_min_bars

        return "trend" if trend_like else "range"

    def _current_equity(self, close: float, ts: object) -> float:
        equity = self.cash
        if self.position != 0 and self.entry_price is not None and self.position_units > 0:
            side = self.entry_side or ("LONG" if self.position > 0 else "SHORT")
            unreal = pnl_to_account(
                self.base_ccy,
                self.quote_ccy,
                self.entry_price,
                close,
                self.position_units,
                account_ccy=self.account_ccy,
                fx_rates=self.fx_rates,
                timestamp=ts,
                side=side,
            )
            equity += unreal
        return equity

    def _check_drawdown(self, equity: float) -> None:
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        if self.max_drawdown_pct:
            if self.peak_equity and self.peak_equity > 0:
                dd = (equity - self.peak_equity) / self.peak_equity
                if dd <= -abs(self.max_drawdown_pct):
                    if not self.trade_halted:
                        logger.warning(f"Drawdown {dd:.2%} exceeds limit; halting new entries.")
                    self.trade_halted = True

    def _determine_units(self, signal_units: Optional[float], stop_price: Optional[float],
                         entry_price: float, ts: object) -> float:
        target_units = float(signal_units) if signal_units is not None else self.default_qty
        final_units = target_units
        if self.risk_per_trade_pct and stop_price is not None and self.initial_cash is not None:
            equity = self.equity_series[-1][1] if self.equity_series else self.initial_cash
            risk_amount = equity * abs(self.risk_per_trade_pct)
            stop_distance = abs(entry_price - stop_price)
            if stop_distance > 0:
                risk_per_unit_quote = stop_distance
                risk_per_unit_account = abs(convert_currency(
                    risk_per_unit_quote,
                    self.quote_ccy,
                    self.account_ccy,
                    self.fx_rates,
                    ts,
                ))
                if risk_per_unit_account > 0:
                    risk_based_units = risk_amount / risk_per_unit_account
                    final_units = min(final_units, risk_based_units)
        if self.max_position_units is not None:
            final_units = min(final_units, self.max_position_units)
        return max(final_units, 0.0)

    # ---- Cost & slippage helpers -------------------------------------------------
    def _normalize_cost_profiles(
        self,
        profiles: Optional[Any],
        fallback_spread: float,
        fallback_slip: float,
        fallback_comm: float,
    ) -> Tuple[List[dict], dict]:
        default_profile = {
            "name": "base_default",
            "spread": float(fallback_spread),
            "slip": float(fallback_slip),
            "comm": float(fallback_comm),
            "start_hour": None,
            "end_hour": None,
            "weekdays": None,
            "priority": float("inf"),
        }
        if not profiles:
            return [], default_profile
        iterable = profiles if isinstance(profiles, (list, tuple)) else [profiles]
        normalized: List[dict] = []
        for idx, raw in enumerate(iterable):
            if not isinstance(raw, Mapping):
                continue
            entry = {
                "name": str(raw.get("name") or f"profile_{idx}"),
                "spread": float(raw.get("spread", fallback_spread)),
                "slip": float(raw.get("slip", fallback_slip)),
                "comm": float(raw.get("comm", fallback_comm)),
                "start_hour": self._coerce_hour(raw.get("start_hour")),
                "end_hour": self._coerce_hour(raw.get("end_hour")),
                "weekdays": self._coerce_weekdays(raw.get("weekdays")),
                "priority": float(raw.get("priority", idx)),
            }
            if raw.get("default"):
                default_profile = entry
            else:
                normalized.append(entry)
        normalized.sort(key=lambda p: p.get("priority", 0))
        return normalized, default_profile

    @staticmethod
    def _coerce_hour(value) -> Optional[float]:
        if value is None:
            return None
        try:
            hour = float(value)
        except Exception:
            return None
        if hour < 0:
            hour = 0.0
        elif hour > 24:
            hour = 24.0
        return hour

    @staticmethod
    def _coerce_weekdays(value) -> Optional[set]:
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set)):
            return None
        weekdays = set()
        for item in value:
            try:
                idx = int(item)
            except Exception:
                continue
            if 0 <= idx <= 6:
                weekdays.add(idx)
        return weekdays or None

    def _maybe_switch_cost_profile(self, ts) -> None:
        profile = self._select_cost_profile(ts)
        if profile is None:
            return
        name = profile.get("name")
        if self.active_cost_profile_name == name:
            return
        self._apply_cost_profile(profile)
        self.active_cost_profile_name = name

    def _select_cost_profile(self, ts) -> dict:
        if not self.cost_profiles:
            return self.default_cost_profile
        dt = self._ensure_datetime(ts)
        for profile in self.cost_profiles:
            if self._cost_profile_matches(profile, dt):
                return profile
        return self.default_cost_profile

    def _cost_profile_matches(self, profile: Mapping[str, Any], dt: Optional[datetime]) -> bool:
        if dt is None:
            return False
        weekdays = profile.get("weekdays")
        if weekdays is not None and dt.weekday() not in weekdays:
            return False
        start = profile.get("start_hour")
        end = profile.get("end_hour")
        if start is not None or end is not None:
            start = 0.0 if start is None else float(start)
            end = 24.0 if end is None else float(end)
            hour = dt.hour + dt.minute / 60.0
            if start < end:
                if not (start <= hour < end):
                    return False
            else:  # overnight window
                if not (hour >= start or hour < end):
                    return False
        return True

    def _apply_cost_profile(self, profile: Mapping[str, Any]) -> None:
        self.spread_pips = float(profile.get("spread", self.base_spread_pips))
        self.profile_slip_pips = float(profile.get("slip", self.base_slippage_pips))
        self.commission_per_million = float(profile.get("comm", self.base_commission_per_million))
        self.half_spread = self.spread_pips * self.pip / 2.0
        self._refresh_slippage_value()

    def _refresh_slippage_value(self) -> None:
        model = self.slippage_model or {}
        base_pips = model.get("base_pips", self.profile_slip_pips)
        try:
            base_pips = float(base_pips)
        except Exception:
            base_pips = self.profile_slip_pips
        slip_pips = max(base_pips, 0.0)
        mode = str(model.get("mode", "fixed")).lower()
        if mode == "atr" and self.curr_atr is not None and self.pip > 0:
            atr_mult = float(model.get("atr_mult", 0.0))
            slip_pips += atr_mult * max(self.curr_atr, 0.0) / self.pip
        size_mult = float(model.get("size_mult", 0.0))
        if size_mult != 0.0:
            pivot = float(model.get("size_pivot", self.default_qty or 1.0))
            if pivot <= 0:
                pivot = 1.0
            ref_units = abs(self.position_units) if self.position_units else float(self.default_qty or pivot)
            size_factor = max(ref_units - pivot, 0.0) / pivot
            slip_pips += size_mult * size_factor
        min_pips = model.get("min_pips")
        max_pips = model.get("max_pips")
        if min_pips is not None:
            slip_pips = max(slip_pips, float(min_pips))
        if max_pips is not None:
            slip_pips = min(slip_pips, float(max_pips))
        self.slippage_pips = slip_pips
        self.slip = slip_pips * self.pip

    @staticmethod
    def _ensure_datetime(value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if hasattr(value, "to_pydatetime"):
            try:
                return value.to_pydatetime()
            except Exception:
                pass
        try:
            return pd.Timestamp(value).to_pydatetime()
        except Exception:
            return None

    def handle_bar(self, bar: dict) -> None:
        ts = bar["ts"]
        mult = max(1.0, self.stress_price_vol_mult)
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        if mult != 1.0:
            mid = (high + low) / 2.0
            half_range = (high - low) / 2.0 * mult
            high = mid + half_range
            low = mid - half_range
            close = mid + (close - mid) * mult
        is_outlier_bar = bool(bar.get("outlier"))

        self.bar_idx += 1
        self.bar_count += 1

        self._maybe_switch_cost_profile(ts)
        self._update_atr(high, low, close)
        self._refresh_slippage_value()
        self.record_close(close)

        equity = self._current_equity(close, ts)
        self.equity_series.append((ts, equity))
        self._check_drawdown(equity)

        self.q_close_fast.append(close)
        self.q_close_slow.append(close)
        if self.q_close_rsi is not None:
            try:
                self.q_close_rsi.append(close)
                self.last_rsi = self._compute_rsi_from_deque()
            except Exception:
                self.last_rsi = None
        else:
            self.last_rsi = None

        self._update_trailing_stop(close)
        self._update_regime_ema(close)
        self.regime_label = self._current_regime_label()
        regime_trend = self.regime_label == "trend"

        if self._check_risk_exit(ts, high, low, close):
            equity = self._current_equity(close, ts)
            self.equity_series[-1] = (ts, equity)

        if len(self.q_close_fast) < self.fast_win or len(self.q_close_slow) < self.slow_win:
            return

        sma_fast = float(np.mean(self.q_close_fast))
        sma_slow = float(np.mean(self.q_close_slow))
        self.sma_fast_hist.append(sma_fast)
        atr_pct = self.curr_atr_percentile
        trend_strength = (abs(self.regime_slope or 0.0) if self.regime_slope is not None else 0.0)
        if atr_pct is not None:
            trend_strength *= atr_pct

        state = {
            "close": close,
            "position": self.position,
            "position_units": self.position_units,
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "curr_atr": self.curr_atr,
            "rsi": self.last_rsi,
            "rsi_period": self.rsi_period,
            "rsi_long_thresh": self.rsi_long_thresh,
            "rsi_short_thresh": self.rsi_short_thresh,
            "bar_idx": self.bar_idx,
            "next_entry_bar_idx_long": self.next_entry_long,
            "next_entry_bar_idx_short": self.next_entry_short,
            "sma_fast_hist": self.sma_fast_hist,
            "equity": equity,
            "regime_label": self.regime_label,
            "regime_trend": regime_trend,
            "regime_slope": self.regime_slope,
            "regime_ema": self.regime_ema,
            "regime_trend_bars": self.regime_trend_bars,
            "atr_percentile": atr_pct,
            "trend_strength": trend_strength,
            "htf_ema": self.htf_ema,
            "htf_rsi": self.htf_rsi,
            "default_qty": self.default_qty,
            "manual_block_remaining": max(0, self.manual_block_until - self.bar_idx),
            "vol_regime": str(bar.get("vol_regime")).lower() if bar.get("vol_regime") is not None else None,
            "trend_regime": str(bar.get("trend_regime")).lower() if bar.get("trend_regime") is not None else None,
            "ts": ts,
            "close_history": tuple(self.q_close_slow),
            "outlier_bar": is_outlier_bar,
        }

        signals = self._gather_strategy_signals(state)
        noted_cooldown = max((sig["cooldown"] for sig in signals), default=0)
        if noted_cooldown > 0:
            self.manual_block_until = max(self.manual_block_until, self.bar_idx + noted_cooldown)

        if self.strategy_combine_mode == "weighted":
            action, signal_units, extra_meta = self._resolve_weighted_action(signals)
        else:
            action, signal_units, extra_meta = self._resolve_first_hit(signals)

        if self.skip_outlier_bars and is_outlier_bar and action.startswith("ENTER"):
            logger.debug(f"Skipping entry on outlier bar @{ts} per configuration.")
            action = "HOLD"
        if self.trade_halted and action.startswith("ENTER"):
            action = "HOLD"
        if self.manual_block_until and self.bar_idx < self.manual_block_until and action.startswith("ENTER"):
            action = "HOLD"

        self._execute_action(action, signal_units, extra_meta, ts, high, low, close)
        equity_after = self._current_equity(close, ts)
        self.equity_series[-1] = (ts, equity_after)

    def _gather_strategy_signals(self, state: dict) -> List[dict]:
        results: List[dict] = []
        for meta in self.strategy_meta:
            strat = meta["instance"]
            sig = strat.on_bar(state) or {}
            results.append({
                "action": sig.get("action", "HOLD"),
                "size": sig.get("size"),
                "cooldown": int(sig.get("cooldown_bars", 0) or 0),
                "weight": float(meta.get("weight", 1.0)),
                "atr_sl_mult": sig.get("atr_sl_mult"),
                "atr_tp_mult": sig.get("atr_tp_mult"),
                "vol_regime": sig.get("vol_regime"),
                "trend_regime": sig.get("trend_regime"),
            })
        return results

    def _resolve_first_hit(self, signals: List[dict]) -> Tuple[str, Optional[float], dict]:
        for sig in signals:
            act = sig["action"]
            if act != "HOLD":
                return act, sig.get("size"), {
                    "atr_sl_mult": sig.get("atr_sl_mult"),
                    "atr_tp_mult": sig.get("atr_tp_mult"),
                    "vol_regime": sig.get("vol_regime"),
                    "trend_regime": sig.get("trend_regime"),
                }
        return "HOLD", None, {}

    def _resolve_weighted_action(self, signals: List[dict]) -> Tuple[str, Optional[float], dict]:
        net_vote = 0.0
        long_exit = 0.0
        short_exit = 0.0
        weighted_size = 0.0
        size_weight_sum = 0.0
        first_meta: dict = {}
        for sig in signals:
            weight = sig["weight"]
            act = sig["action"]
            size = sig.get("size")
            if act == "ENTER_LONG":
                net_vote += weight
                if size is not None:
                    weighted_size += weight * float(size)
                    size_weight_sum += weight
                if not first_meta:
                    first_meta = {
                        "atr_sl_mult": sig.get("atr_sl_mult"),
                        "atr_tp_mult": sig.get("atr_tp_mult"),
                        "vol_regime": sig.get("vol_regime"),
                        "trend_regime": sig.get("trend_regime"),
                    }
            elif act == "ENTER_SHORT":
                net_vote -= weight
                if size is not None:
                    weighted_size += weight * float(size)
                    size_weight_sum += weight
                if not first_meta:
                    first_meta = {
                        "atr_sl_mult": sig.get("atr_sl_mult"),
                        "atr_tp_mult": sig.get("atr_tp_mult"),
                        "vol_regime": sig.get("vol_regime"),
                        "trend_regime": sig.get("trend_regime"),
                    }
            elif act == "EXIT_LONG":
                long_exit += weight
            elif act == "EXIT_SHORT":
                short_exit += weight
        threshold = self.strategy_vote_threshold
        action = "HOLD"
        signal_units = None
        meta = first_meta
        if self.position == 0:
            if net_vote > threshold:
                action = "ENTER_LONG"
            elif net_vote < -threshold:
                action = "ENTER_SHORT"
            if action.startswith("ENTER") and size_weight_sum > 0:
                signal_units = weighted_size / size_weight_sum
        elif self.position == 1 and long_exit > threshold:
            action = "EXIT_LONG"
        elif self.position == -1 and short_exit > threshold:
            action = "EXIT_SHORT"
        return action, signal_units, meta

    def _execute_action(self, action: str, signal_units: Optional[float], signal_meta: dict, ts, high, low, close) -> None:
        if action == "HOLD":
            return
        vol_regime = signal_meta.get("vol_regime")
        trend_regime = signal_meta.get("trend_regime")
        atr_sl_mult = self._resolve_atr_from_map(signal_meta.get("atr_sl_mult"), vol_regime, trend_regime, self.atr_sl_map)
        atr_tp_mult = self._resolve_atr_from_map(signal_meta.get("atr_tp_mult"), vol_regime, trend_regime, self.atr_tp_map)
        if action == "ENTER_LONG":
            if self.position != 0:
                return
            if self._skip_trade_randomly():
                return
            entry_exec = close + self._stressed_half_spread() + self._stressed_slip()
            entry_atr = self.curr_atr
            stop_price, tp_price = self._calc_long_stops(entry_exec, entry_atr, atr_sl_mult, atr_tp_mult)
            units = self._determine_units(signal_units, stop_price, entry_exec, ts)
            if units <= 0:
                return
            notional_acct = notional_in_account(
                entry_exec,
                units,
                self.base_ccy,
                self.quote_ccy,
                account_ccy=self.account_ccy,
                fx_rates=self.fx_rates,
                timestamp=ts,
            )
            commission = self._calc_commission(notional_acct)
            self.cash -= commission
            self.position = 1
            self.position_units = units
            self.entry_price = entry_exec
            self.entry_side = "LONG"
            self.entry_atr = entry_atr
            self.stop_price = stop_price
            self.tp_price = tp_price
            self.trade_count += 1
            self.trade_log.append({
                "ts_entry": ts, "side": "LONG", "qty": units, "entry": entry_exec,
                "commission": commission, "entry_atr": self.entry_atr,
                "stop_price": stop_price, "tp_price": tp_price
            })
            if self.execution_handler:
                self.execution_handler(OrderEvent(ts, self.symbol, "BUY", units))
            atr_str = f"{entry_atr:.5f}" if entry_atr is not None else "nan"
            sl_mode = "ATR" if self.atr_sl is not None else "pips"
            tp_mode = "ATR" if self.atr_tp is not None else "pips"
            logger.info(
                f"BUY  {units:.0f} {self.symbol} @ {entry_exec:.5f}  ts={ts}  "
                f"(SMA {self.fast_win}/{self.slow_win}, ATR={atr_str}, SL={sl_mode}, TP={tp_mode})"
            )
        elif action == "ENTER_SHORT":
            if not self.allow_short or self.position != 0:
                return
            if self._skip_trade_randomly():
                return
            entry_exec = close - self._stressed_half_spread() - self._stressed_slip()
            entry_atr = self.curr_atr
            stop_price, tp_price = self._calc_short_stops(entry_exec, entry_atr, atr_sl_mult, atr_tp_mult)
            units = self._determine_units(signal_units, stop_price, entry_exec, ts)
            if units <= 0:
                return
            notional_acct = notional_in_account(
                entry_exec,
                units,
                self.base_ccy,
                self.quote_ccy,
                account_ccy=self.account_ccy,
                fx_rates=self.fx_rates,
                timestamp=ts,
            )
            commission = self._calc_commission(notional_acct)
            self.cash -= commission
            self.position = -1
            self.position_units = units
            self.entry_price = entry_exec
            self.entry_side = "SHORT"
            self.entry_atr = entry_atr
            self.stop_price = stop_price
            self.tp_price = tp_price
            self.trade_count += 1
            self.trade_log.append({
                "ts_entry": ts, "side": "SHORT", "qty": units, "entry": entry_exec,
                "commission": commission, "entry_atr": self.entry_atr,
                "stop_price": stop_price, "tp_price": tp_price
            })
            if self.execution_handler:
                self.execution_handler(OrderEvent(ts, self.symbol, "SELL", units))
            atr_str = f"{entry_atr:.5f}" if entry_atr is not None else "nan"
            sl_mode = "ATR" if self.atr_sl is not None else "pips"
            tp_mode = "ATR" if self.atr_tp is not None else "pips"
            logger.info(
                f"SELL {units:.0f} {self.symbol} @ {entry_exec:.5f}  ts={ts}  "
                f"(SMA {self.fast_win}/{self.slow_win}, ATR={atr_str}, SL={sl_mode}, TP={tp_mode})"
            )
        elif action == "EXIT_LONG" and self.position == 1:
            if self._skip_trade_randomly():
                return
            exit_exec = close - self._stressed_half_spread() - self._stressed_slip()
            self._exit_position(ts, exit_exec, "Signal")
            if self.cooldown:
                self.next_entry_long = self.bar_idx + self.cooldown
        elif action == "EXIT_SHORT" and self.position == -1:
            if self._skip_trade_randomly():
                return
            exit_exec = close + self._stressed_half_spread() + self._stressed_slip()
            self._exit_position(ts, exit_exec, "Signal")
            if self.cooldown:
                self.next_entry_short = self.bar_idx + self.cooldown

    @staticmethod
    def _resolve_atr_from_map(
        override_mult: Optional[float],
        vol_regime: Optional[str],
        trend_regime: Optional[str],
        mapping: Mapping[str, float],
    ) -> Optional[float]:
        if override_mult is not None:
            return override_mult
        # Trend has higher priority than vol; then default fallback.
        if trend_regime:
            key = str(trend_regime).lower()
            if key in mapping:
                return mapping[key]
        if vol_regime:
            key = str(vol_regime).lower()
            # normalize possible names
            if key == "high" and "vol_high" in mapping:
                return mapping["vol_high"]
            if key == "low" and "vol_low" in mapping:
                return mapping["vol_low"]
            if key == "normal" and "vol_normal" in mapping:
                return mapping["vol_normal"]
            if key in mapping:
                return mapping[key]
        if "default" in mapping:
            return mapping["default"]
        return None

    def _calc_long_stops(self, entry_price: float, entry_atr: Optional[float], atr_sl_mult: Optional[float] = None, atr_tp_mult: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
        stop_price = None
        tp_price = None
        eff_sl = self.atr_sl * atr_sl_mult if (self.atr_sl is not None and atr_sl_mult is not None) else self.atr_sl
        eff_tp = self.atr_tp * atr_tp_mult if (self.atr_tp is not None and atr_tp_mult is not None) else self.atr_tp
        if eff_sl is not None and entry_atr is not None:
            stop_price = entry_price - eff_sl * entry_atr
        elif self.stop_loss_pips is not None:
            stop_price = entry_price - (self.stop_loss_pips * self.pip)
        if eff_tp is not None and entry_atr is not None:
            tp_price = entry_price + eff_tp * entry_atr
        elif self.take_profit_pips is not None:
            tp_price = entry_price + (self.take_profit_pips * self.pip)
        return stop_price, tp_price

    def _calc_short_stops(self, entry_price: float, entry_atr: Optional[float], atr_sl_mult: Optional[float] = None, atr_tp_mult: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
        stop_price = None
        tp_price = None
        eff_sl = self.atr_sl * atr_sl_mult if (self.atr_sl is not None and atr_sl_mult is not None) else self.atr_sl
        eff_tp = self.atr_tp * atr_tp_mult if (self.atr_tp is not None and atr_tp_mult is not None) else self.atr_tp
        if eff_sl is not None and entry_atr is not None:
            stop_price = entry_price + eff_sl * entry_atr
        elif self.stop_loss_pips is not None:
            stop_price = entry_price + (self.stop_loss_pips * self.pip)
        if eff_tp is not None and entry_atr is not None:
            tp_price = entry_price - eff_tp * entry_atr
        elif self.take_profit_pips is not None:
            tp_price = entry_price - (self.take_profit_pips * self.pip)
        return stop_price, tp_price

    def _check_risk_exit(self, ts, high: float, low: float, close: float) -> bool:
        if self.position == 0 or self.entry_price is None or self.position_units <= 0:
            return False
        triggered = False
        exit_reason = None
        exit_exec = None
        if self.position == 1:
            if self.stop_price is not None and low <= self.stop_price:
                exit_reason = "SL"
                exit_exec = max(self.stop_price - self.half_spread - self.slip, low - self.half_spread - self.slip)
            elif self.tp_price is not None and high >= self.tp_price:
                exit_reason = "TP"
                exit_exec = min(self.tp_price - self.half_spread - self.slip, high - self.half_spread - self.slip)
        elif self.position == -1:
            if self.stop_price is not None and high >= self.stop_price:
                exit_reason = "SL"
                exit_exec = min(self.stop_price + self.half_spread + self.slip, high + self.half_spread + self.slip)
            elif self.tp_price is not None and low <= self.tp_price:
                exit_reason = "TP"
                exit_exec = max(self.tp_price + self.half_spread + self.slip, low + self.half_spread + self.slip)
        if exit_reason and exit_exec is not None:
            side_before = self.entry_side or ("LONG" if self.position > 0 else "SHORT")
            self._exit_position(ts, exit_exec, exit_reason)
            if self.cooldown and side_before:
                if side_before == "LONG":
                    self.next_entry_long = self.bar_idx + self.cooldown
                elif side_before == "SHORT":
                    self.next_entry_short = self.bar_idx + self.cooldown
            triggered = True
        return triggered

    def _exit_position(self, ts, exit_exec: float, reason: str) -> None:
        if self.position == 0 or self.entry_price is None or self.position_units <= 0:
            return
        side = self.entry_side or ("LONG" if self.position > 0 else "SHORT")
        notional_acct = notional_in_account(
            exit_exec,
            self.position_units,
            self.base_ccy,
            self.quote_ccy,
            account_ccy=self.account_ccy,
            fx_rates=self.fx_rates,
            timestamp=ts,
        )
        commission = self._calc_commission(notional_acct)
        gross = pnl_to_account(
            self.base_ccy,
            self.quote_ccy,
            self.entry_price,
            exit_exec,
            self.position_units,
            account_ccy=self.account_ccy,
            fx_rates=self.fx_rates,
            timestamp=ts,
            side=side,
        )
        pnl = gross - commission
        self.pnl_realized += pnl
        self.cash += pnl
        self.trade_count += 1
        self.trade_log.append({
            "ts_exit": ts,
            "side": side,
            "reason": reason,
            "qty": self.position_units,
            "entry": self.entry_price,
            "exit": exit_exec,
            "commission": commission,
            "pnl": pnl,
            "entry_atr": self.entry_atr,
            "stop_price": self.stop_price,
            "tp_price": self.tp_price,
        })
        if self.execution_handler:
            close_side = "SELL" if side == "LONG" else "BUY"
            self.execution_handler(OrderEvent(ts, self.symbol, close_side, self.position_units))
        if side == "LONG":
            logger.info(f"SELL {self.position_units:.0f} {self.symbol} @ {exit_exec:.5f}  PnL={pnl:.2f}  ts={ts} ({reason})")
        else:
            logger.info(f"BUY  {self.position_units:.0f} {self.symbol} @ {exit_exec:.5f}  PnL={pnl:.2f}  ts={ts} ({reason})")
        self.position = 0
        self.position_units = 0.0
        self.entry_price = None
        self.entry_side = None
        self.entry_atr = None
        self.stop_price = None
        self.tp_price = None

    def finalize(self) -> None:
        if self.position == 0 or self.entry_price is None or self.last_close is None:
            return
        last_ts = self.equity_series[-1][0] if self.equity_series else None
        if last_ts is None:
            return
        if self.position == 1:
            exit_exec = self.last_close - self.half_spread - self.slip
        else:
            exit_exec = self.last_close + self.half_spread + self.slip
        self._exit_position(last_ts, exit_exec, "mark_to_market")
        equity = self.cash
        if self.equity_series:
            if self.equity_series[-1][0] == last_ts:
                self.equity_series[-1] = (last_ts, equity)
            else:
                self.equity_series.append((last_ts, equity))
        else:
            self.equity_series.append((last_ts, equity))

    def record_close(self, close: float) -> None:
        self.last_close = close

    def compute_suffix(self) -> str:
        tags = []
        if self.atr_sl is not None or self.atr_tp is not None:
            tags.append(f"ATR{self.atr_sl if self.atr_sl is not None else 'None'}x{self.atr_tp if self.atr_tp is not None else 'None'}_W{self.atr_window}")
        else:
            tags.append(f"SL{self.stop_loss_pips if self.stop_loss_pips is not None else 'None'}xTP{self.take_profit_pips if self.take_profit_pips is not None else 'None'}")
        if self.long_only_above_slow:
            tags.append("ABOVE")
        if self.allow_short:
            tags.append("SHORT")
        else:
            tags.append("LONGONLY")
        if self.allow_short and self.short_only_below_slow:
            tags.append("BELOW")
        if self.slope_lookback:
            tags.append(f"SLOPE{self.slope_lookback}")
        if self.cooldown:
            tags.append(f"CD{self.cooldown}")
        return "_".join(tags)

    def export_outputs(self, fast_win: int, slow_win: int, suffix: str) -> dict:
        equity_dir = Path(self.output_dirs["equity"])
        trades_dir = Path(self.output_dirs["trades"])
        stats_dir = Path(self.output_dirs["stats"])
        equity_dir.mkdir(parents=True, exist_ok=True)
        trades_dir.mkdir(parents=True, exist_ok=True)
        stats_dir.mkdir(parents=True, exist_ok=True)
        files = {}

        if self.equity_series:
            equity_file = equity_dir / f"equity_{self.symbol}_H1_{fast_win}x{slow_win}_{suffix}.csv"
            pd.DataFrame(self.equity_series, columns=["ts", "equity"]).to_csv(equity_file, index=False)
            files["equity"] = str(equity_file)
        if self.trade_log:
            trade_file = trades_dir / f"trades_{self.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            pd.DataFrame(self.trade_log).to_csv(trade_file, index=False)
            files["trades"] = str(trade_file)
            stats = trade_stats(self.trade_log)
            if stats:
                rr_val = stats.get("rr")
                rr_str = f"{rr_val:.2f}" if rr_val is not None else "nan"
                logger.info(
                    f"胜率={stats['win_rate']:.2%}  盈亏比={rr_str}  单笔期望=${stats['expectancy']:.2f}  中位持仓={stats['median_hold']}"
                )
                stats_file = stats_dir / f"stats_{self.symbol}_H1_{fast_win}x{slow_win}_{suffix}.json"
                with stats_file.open("w", encoding="utf-8") as f:
                    json.dump(stats, f, ensure_ascii=False, indent=2)
                files["trade_stats"] = str(stats_file)
        return files

    def summary(self, fast_win: int, slow_win: int, suffix: Optional[str] = None) -> dict:
        metrics = compute_metrics(self.equity_series, bars_per_year=24 * 252)
        result = {
            "fast": fast_win,
            "slow": slow_win,
            "final_equity": metrics.get("final_equity") if metrics else (self.equity_series[-1][1] if self.equity_series else self.cash),
            "ann_return": metrics.get("ann_return") if metrics else None,
            "ann_vol": metrics.get("ann_vol") if metrics else None,
            "sharpe": metrics.get("sharpe") if metrics else None,
            "max_drawdown": metrics.get("max_drawdown") if metrics else None,
            "sortino": metrics.get("sortino") if metrics else None,
            "calmar": metrics.get("calmar") if metrics else None,
            "max_drawdown_duration_bars": metrics.get("max_drawdown_duration_bars") if metrics else None,
            "avg_drawdown_duration_bars": metrics.get("avg_drawdown_duration_bars") if metrics else None,
            "current_drawdown_duration_bars": metrics.get("current_drawdown_duration_bars") if metrics else None,
            "recovery_time_bars": metrics.get("recovery_time_bars") if metrics else None,
            "trades": self.trade_count,
            "atr_sl": self.atr_sl,
            "atr_tp": self.atr_tp,
            "atr_window": self.atr_window,
        }
        return result


    def _stressed_half_spread(self) -> float:
        return self.half_spread * self.stress_cost_spread_mult

    def _stressed_slip(self) -> float:
        return self.slip * self.stress_slippage_mult

    def _skip_trade_randomly(self) -> bool:
        if self.stress_skip_trade_pct <= 0:
            return False
        return np.random.random() < self.stress_skip_trade_pct


__all__ = [
    "StrategyEngine",
    "StrategySpec",
    "parse_strategy_specs",
    "FXRateProvider",
    "_coerce_fx_rates",
    "_merge_fx_rates",
    "convert_currency",
    "notional_in_account",
    "pnl_to_account",
]
