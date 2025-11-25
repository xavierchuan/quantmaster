from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import register
from .base import Strategy


def _rsi(values: Sequence[float], length: int) -> Optional[float]:
    arr = np.asarray(values, dtype=float)
    if arr.size <= length:
        return None
    diffs = np.diff(arr[-(length + 1) :])
    gains = diffs[diffs > 0]
    losses = -diffs[diffs < 0]
    avg_gain = gains.mean() if gains.size else 0.0
    avg_loss = losses.mean() if losses.size else 0.0
    if avg_loss == 0.0 and avg_gain == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _zscore(values: Sequence[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return None, None, None
    mean = arr.mean()
    std = arr.std(ddof=0)
    if std <= 1e-9:
        return None, mean, std
    return (arr[-1] - mean) / std, mean, std


def _in_blocked_session(ts: Any, windows: Iterable[Tuple[float, float]]) -> bool:
    try:
        hour = float(pd.Timestamp(ts).hour)
    except Exception:
        return False
    for start, end in windows:
        if start < end:
            if start <= hour < end:
                return True
        else:  # overnight window, e.g., [22, 6]
            if hour >= start or hour < end:
                return True
    return False


@register("mean_revert_micro")
class MeanRevertMicro(Strategy):
    """
    Micro mean-reversion helper strategy:
    - Only trades pullbacks in up-trend (by default).
    - Entry: RSI(2/3) oversold + price < lower Bollinger + negative z-score.
    - Filters: vol_regime/trend_regime gating, ATR percentile band, session block.
    - Exits: revert to mid-band / z-score mean, time-stop, or high-vol appearance.
    """

    def __init__(
        self,
        rsi_length: int = 2,
        rsi_long_th: float = 10.0,
        zscore_length: int = 20,
        zscore_long_th: float = -1.0,
        bb_length: int = 20,
        bb_width: float = 2.0,
        atr_pct_min: float = 0.2,
        atr_pct_max: float = 0.85,
        vol_allow: Optional[List[str]] = None,
        trend_allow: Optional[List[str]] = None,
        session_block_hours: Optional[List[List[float]]] = None,
        size_mult: float = 0.25,
        atr_sl_mult: float = 1.0,
        atr_tp_mult: float = 0.6,
        time_stop_bars: int = 12,
        cooldown_bars: int = 4,
        exit_zscore_th: float = -0.2,
        exit_on_high_vol: bool = True,
        allow_short: bool = False,
        extension_lookback: int = 3,
        extension_min_red: int = 2,
        extension_body_atr_min: float = 0.4,
        dynamic_size_scale: bool = True,
        dynamic_size_clip_min: float = 0.5,
        dynamic_size_clip_max: float = 1.5,
        max_size_mult: float = 0.35,
        min_reentry_gap_bars: int = 5,
        exit_on_trend_disallow: bool = True,
    ) -> None:
        super().__init__()
        self.rsi_length = int(rsi_length)
        self.rsi_long_th = float(rsi_long_th)
        self.zscore_length = int(zscore_length)
        self.zscore_long_th = float(zscore_long_th)
        self.bb_length = int(bb_length)
        self.bb_width = float(bb_width)
        self.atr_pct_min = float(atr_pct_min) if atr_pct_min is not None else None
        self.atr_pct_max = float(atr_pct_max) if atr_pct_max is not None else None
        self.vol_allow = {str(v).lower() for v in (vol_allow or ["vol_low", "vol_norm", "vol_med", "vol_normal"])}
        self.trend_allow = {str(t).lower() for t in (trend_allow or ["trend_up"])}
        self.session_block_hours = [(float(a), float(b)) for a, b in (session_block_hours or [])]
        self.size_mult = float(size_mult)
        self.atr_sl_mult = float(atr_sl_mult)
        self.atr_tp_mult = float(atr_tp_mult)
        self.time_stop_bars = int(time_stop_bars)
        self.cooldown_bars = int(cooldown_bars)
        self.exit_zscore_th = float(exit_zscore_th)
        self.exit_on_high_vol = bool(exit_on_high_vol)
        self.allow_short = bool(allow_short)
        self.extension_lookback = int(extension_lookback)
        self.extension_min_red = int(extension_min_red)
        self.extension_body_atr_min = float(extension_body_atr_min)
        self.dynamic_size_scale = bool(dynamic_size_scale)
        self.dynamic_size_clip_min = float(dynamic_size_clip_min)
        self.dynamic_size_clip_max = float(dynamic_size_clip_max)
        self.max_size_mult = float(max_size_mult)
        self.min_reentry_gap_bars = int(min_reentry_gap_bars)
        self.exit_on_trend_disallow = bool(exit_on_trend_disallow)
        self.last_entry_bar: Optional[int] = None

    def on_bar(self, state: Dict[str, Any]) -> Dict[str, Any]:
        close_history = state.get("close_history")
        if close_history is None:
            return {"action": "HOLD"}
        closes = np.asarray(close_history, dtype=float)
        if closes.size < max(self.bb_length, self.zscore_length) + 1:
            return {"action": "HOLD"}

        ts = state.get("ts")
        if self.session_block_hours and _in_blocked_session(ts, self.session_block_hours):
            return {"action": "HOLD"}

        vol_regime = str(state.get("vol_regime") or "").lower() or None
        trend_regime = str(state.get("trend_regime") or "").lower() or None
        if trend_regime not in self.trend_allow:
            return {"action": "HOLD"}
        if self.vol_allow and vol_regime not in self.vol_allow:
            return {"action": "HOLD"}

        atr_pct = state.get("atr_percentile")
        if atr_pct is not None:
            atr_pct = float(atr_pct)
            if self.atr_pct_min is not None and atr_pct < self.atr_pct_min:
                return {"action": "HOLD"}
            if self.atr_pct_max is not None and atr_pct > self.atr_pct_max:
                return {"action": "HOLD"}

        close = float(closes[-1])
        rsi = _rsi(closes, self.rsi_length)
        if rsi is None:
            return {"action": "HOLD"}

        zs, mean, std = _zscore(closes[-self.zscore_length :])
        if zs is None or mean is None or std is None or std <= 1e-9:
            return {"action": "HOLD"}
        mid = mean
        lower = mid - self.bb_width * std
        upper = mid + self.bb_width * std  # for completeness if allow_short

        bar_idx = int(state.get("bar_idx", 0) or 0)
        position = int(state.get("position", 0) or 0)
        default_qty = float(state.get("default_qty", 0.0) or 0.0)

        # Reset book-keeping when flat
        if position == 0:
            self.last_entry_bar = None

        # Avoid rapid re-entries into same pullback
        if position == 0 and self.last_entry_bar is not None:
            if bar_idx - self.last_entry_bar < self.min_reentry_gap_bars:
                return {"action": "HOLD"}

        # Extension filter: require recent downward extension before buying a dip
        def _has_extension() -> bool:
            if self.extension_lookback <= 0 or self.extension_min_red <= 0:
                return True
            if closes.size < self.extension_lookback + 1:
                return False
            recent = closes[-(self.extension_lookback + 1) :]
            diffs = np.diff(recent)
            curr_atr = state.get("curr_atr")
            if curr_atr is None or curr_atr <= 0:
                return False
            bodies = np.abs(diffs)
            red = (diffs < 0) & (bodies >= self.extension_body_atr_min * curr_atr)
            return red.sum() >= self.extension_min_red

        extension_ok = _has_extension()

        # Dynamic size based on z-score magnitude
        scaled_mult = self.size_mult
        if self.dynamic_size_scale and zs is not None:
            factor = np.clip(abs(zs) / 2.0, self.dynamic_size_clip_min, self.dynamic_size_clip_max)
            scaled_mult = min(self.size_mult * factor, self.max_size_mult)

        # Entry logic (long)
        if position == 0:
            if extension_ok and rsi <= self.rsi_long_th and zs <= self.zscore_long_th and close <= lower:
                self.last_entry_bar = bar_idx
                return {
                    "action": "ENTER_LONG",
                    "size": default_qty * scaled_mult,
                    "cooldown_bars": self.cooldown_bars,
                    "atr_sl_mult": self.atr_sl_mult,
                    "atr_tp_mult": self.atr_tp_mult,
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                }
            if (
                self.allow_short
                and extension_ok
                and rsi >= 100 - self.rsi_long_th
                and zs >= -self.zscore_long_th
                and close >= upper
            ):
                self.last_entry_bar = bar_idx
                return {
                    "action": "ENTER_SHORT",
                    "size": default_qty * scaled_mult,
                    "cooldown_bars": self.cooldown_bars,
                    "atr_sl_mult": self.atr_sl_mult,
                    "atr_tp_mult": self.atr_tp_mult,
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                }

        # Exit logic if we (likely) drove the entry
        if position > 0 and self.last_entry_bar is not None:
            timed_out = self.time_stop_bars and (bar_idx - self.last_entry_bar >= self.time_stop_bars)
            revert_hit = close >= mid or zs >= self.exit_zscore_th
            high_vol_exit = self.exit_on_high_vol and vol_regime == "vol_high"
            trend_disallow_exit = self.exit_on_trend_disallow and trend_regime not in self.trend_allow
            if timed_out or revert_hit or high_vol_exit or trend_disallow_exit:
                return {
                    "action": "EXIT_LONG",
                    "cooldown_bars": 0,
                    "atr_sl_mult": self.atr_sl_mult,
                    "atr_tp_mult": self.atr_tp_mult,
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                }

        if position < 0 and self.allow_short and self.last_entry_bar is not None:
            timed_out = self.time_stop_bars and (bar_idx - self.last_entry_bar >= self.time_stop_bars)
            revert_hit = close <= mid or zs <= -self.exit_zscore_th
            high_vol_exit = self.exit_on_high_vol and vol_regime == "vol_high"
            trend_disallow_exit = self.exit_on_trend_disallow and trend_regime not in self.trend_allow
            if timed_out or revert_hit or high_vol_exit or trend_disallow_exit:
                return {
                    "action": "EXIT_SHORT",
                    "cooldown_bars": 0,
                    "atr_sl_mult": self.atr_sl_mult,
                    "atr_tp_mult": self.atr_tp_mult,
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                }

        return {"action": "HOLD"}
