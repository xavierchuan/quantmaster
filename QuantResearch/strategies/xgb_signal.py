"""XGBoost-based signal strategy (long-only v1).

Loads a trained Booster + feature list + thresholds and emits ENTER_LONG/EXIT_LONG
decisions based on predicted probability compared to configured thresholds.

Registration name: "xgb_signal"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from . import register
from .base import Strategy


def _safe_get(d: Dict[str, Any], key: str, default=None):
    v = d.get(key, default)
    return v if v is not None else default


def _rsi_from_series(arr: np.ndarray, period: int = 14) -> float | None:
    if arr.size < period + 1:
        return None
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
    return 100.0 - (100.0 / (1.0 + rs))


@register("xgb_signal")
class XGBSignal(Strategy):
    def __init__(
        self,
        model_dir: Optional[str] = None,
        latest_ptr: str = "QuantResearch/artifacts/models/usdjpy_h1_xgb_latest.json",
        trend_ptr: Optional[str] = None,
        prob_long: Optional[float] = None,
        prob_exit: Optional[float] = None,
        size_mult: float = 1.0,
        cooldown_bars: int = 0,
        min_atr_pct: Optional[float] = None,
        low_atr_pct: Optional[float] = None,
        prob_long_low: Optional[float] = None,
        cooldown_low: Optional[int] = None,
        min_vol_24: Optional[float] = None,
        atr_relax_pct: Optional[float] = None,
        prob_long_relaxed: Optional[float] = None,
        cooldown_relaxed: Optional[int] = None,
        vol_high_prob_delta: Optional[float] = None,
        vol_low_prob_delta: Optional[float] = None,
        vol_high_size_mult: Optional[float] = None,
        vol_low_size_mult: Optional[float] = None,
        vol_high_cooldown: Optional[int] = None,
        vol_low_cooldown: Optional[int] = None,
        vol_high_atr_sl_mult: Optional[float] = None,
        vol_high_atr_tp_mult: Optional[float] = None,
        vol_low_atr_sl_mult: Optional[float] = None,
        vol_low_atr_tp_mult: Optional[float] = None,
        enable_short_signals: bool = False,
        prob_short: Optional[float] = None,
        prob_short_exit: Optional[float] = None,
        short_size_mult: float = 0.8,
        allow_short_regimes: Optional[List[str]] = None,
        short_cooldown_bars: Optional[int] = None,
        short_vol_high_size_mult: Optional[float] = None,
        short_vol_low_size_mult: Optional[float] = None,
        short_vol_high_prob_delta: Optional[float] = None,
        short_vol_low_prob_delta: Optional[float] = None,
        short_vol_high_cooldown: Optional[int] = None,
        short_vol_low_cooldown: Optional[int] = None,
        trend_up_prob_delta: Optional[float] = None,
        trend_down_prob_delta: Optional[float] = None,
        trend_chop_prob_delta: Optional[float] = None,
        trend_up_short_prob_delta: Optional[float] = None,
        trend_down_short_prob_delta: Optional[float] = None,
        trend_chop_short_prob_delta: Optional[float] = None,
        trend_up_size_mult: Optional[float] = None,
        trend_down_size_mult: Optional[float] = None,
        trend_chop_size_mult: Optional[float] = None,
        trend_up_short_size_mult: Optional[float] = None,
        trend_down_short_size_mult: Optional[float] = None,
        trend_chop_short_size_mult: Optional[float] = None,
        trend_up_cooldown: Optional[int] = None,
        trend_down_cooldown: Optional[int] = None,
        trend_chop_cooldown: Optional[int] = None,
        trend_up_short_cooldown: Optional[int] = None,
        trend_down_short_cooldown: Optional[int] = None,
        trend_chop_short_cooldown: Optional[int] = None,
        trend_up_atr_sl_mult: Optional[float] = None,
        trend_up_atr_tp_mult: Optional[float] = None,
        trend_down_atr_sl_mult: Optional[float] = None,
        trend_down_atr_tp_mult: Optional[float] = None,
        trend_chop_atr_sl_mult: Optional[float] = None,
        trend_chop_atr_tp_mult: Optional[float] = None,
        session_block_hours: Optional[List[List[float]]] = None,
        regime_filter: Optional[Dict[str, Any]] = None,
        debug_log_hits: bool = False,
    ) -> None:
        super().__init__()
        # Resolve model directory
        if not model_dir:
            ptr = Path(latest_ptr)
            if not ptr.exists():
                raise RuntimeError(f"latest.json not found: {ptr}")
            latest = json.loads(ptr.read_text(encoding="utf-8"))
            model_dir = latest.get("model_dir")
            if not model_dir:
                raise RuntimeError("latest.json missing 'model_dir'")
        self.model_dir = Path(model_dir)
        # Load artifacts
        self.feature_list: List[str] = json.loads((self.model_dir / "feature_list.json").read_text(encoding="utf-8"))
        thr = json.loads((self.model_dir / "thresholds.json").read_text(encoding="utf-8"))
        self.label_mode = thr.get("label_mode", "binary")
        self.p_long = float(prob_long) if prob_long is not None else float(thr.get("p_long", 0.6))
        self.p_exit = float(prob_exit) if prob_exit is not None else float(thr.get("p_exit", 0.5))
        # Trend model placeholders (optional)
        self.trend_ptr = trend_ptr
        self.trend_model = None
        self.trend_feature_list: List[str] = []
        self.trend_mapping = {"trend_down": 0, "chop": 1, "trend_up": 2}
        model_supports_short = self.label_mode == "multi" and "p_short" in thr
        self.enable_short = bool(enable_short_signals and model_supports_short)
        self.p_short = float(prob_short) if prob_short is not None else float(thr.get("p_short", self.p_long)) if model_supports_short else None
        self.p_short_exit = float(prob_short_exit) if prob_short_exit is not None else float(thr.get("p_short_exit", self.p_exit)) if model_supports_short else None
        self.short_size_mult = float(short_size_mult)
        self.short_cooldown_bars = int(max(0, short_cooldown_bars)) if short_cooldown_bars is not None else None
        self.short_vol_high_size_mult = float(short_vol_high_size_mult) if short_vol_high_size_mult is not None else None
        self.short_vol_low_size_mult = float(short_vol_low_size_mult) if short_vol_low_size_mult is not None else None
        self.short_vol_high_prob_delta = float(short_vol_high_prob_delta) if short_vol_high_prob_delta is not None else None
        self.short_vol_low_prob_delta = float(short_vol_low_prob_delta) if short_vol_low_prob_delta is not None else None
        self.short_vol_high_cooldown = int(max(0, short_vol_high_cooldown)) if short_vol_high_cooldown is not None else None
        self.short_vol_low_cooldown = int(max(0, short_vol_low_cooldown)) if short_vol_low_cooldown is not None else None
        if allow_short_regimes:
            if isinstance(allow_short_regimes, str):
                allow_short_regimes = [allow_short_regimes]
            self.allow_short_regimes = {str(x).lower() for x in allow_short_regimes}
        else:
            self.allow_short_regimes = set()
        try:
            import xgboost as xgb
        except Exception as exc:
            raise RuntimeError("xgboost is required at runtime for xgb_signal.") from exc
        self._xgb = xgb
        self._booster = xgb.Booster()
        self._booster.load_model(str(self.model_dir / "model.json"))
        # Load trend model if provided
        if self.trend_ptr:
            try:
                trend_payload = json.loads(Path(self.trend_ptr).read_text(encoding="utf-8"))
                t_dir = trend_payload.get("model_dir")
                if t_dir:
                    t_dir_path = Path(t_dir)
                    self.trend_feature_list = json.loads((t_dir_path / "feature_list.json").read_text(encoding="utf-8"))
                    t_thr = json.loads((t_dir_path / "thresholds.json").read_text(encoding="utf-8"))
                    mapping = t_thr.get("class_mapping")
                    if isinstance(mapping, dict):
                        self.trend_mapping = mapping
                    self.trend_model = xgb.Booster()
                    self.trend_model.load_model(str(t_dir_path / "model.json"))
                    logger.info(f"[xgb_signal] trend model loaded from {t_dir_path}")
            except Exception as exc:
                logger.warning(f"[xgb_signal] failed to load trend model {self.trend_ptr}: {exc}")
                self.trend_model = None

        self.size_mult = float(size_mult)
        self.cooldown_bars = int(max(0, cooldown_bars))
        self.cooldown_relaxed = int(max(0, cooldown_relaxed)) if cooldown_relaxed is not None else None
        self.min_atr_pct = float(min_atr_pct) if min_atr_pct is not None else None
        self.low_atr_pct = float(low_atr_pct) if low_atr_pct is not None else None
        self.prob_long_low = float(prob_long_low) if prob_long_low is not None else None
        self.cooldown_low = int(max(0, cooldown_low)) if cooldown_low is not None else None
        self.min_vol_24 = float(min_vol_24) if min_vol_24 is not None else None
        self.atr_relax_pct = float(atr_relax_pct) if atr_relax_pct is not None else None
        self.p_long_relaxed = float(prob_long_relaxed) if prob_long_relaxed is not None else None
        self.vol_high_prob_delta = float(vol_high_prob_delta) if vol_high_prob_delta is not None else None
        self.vol_low_prob_delta = float(vol_low_prob_delta) if vol_low_prob_delta is not None else None
        self.vol_high_size_mult = float(vol_high_size_mult) if vol_high_size_mult is not None else None
        self.vol_low_size_mult = float(vol_low_size_mult) if vol_low_size_mult is not None else None
        self.vol_high_cooldown = int(max(0, vol_high_cooldown)) if vol_high_cooldown is not None else None
        self.vol_low_cooldown = int(max(0, vol_low_cooldown)) if vol_low_cooldown is not None else None
        self.vol_high_atr_sl_mult = float(vol_high_atr_sl_mult) if vol_high_atr_sl_mult is not None else None
        self.vol_high_atr_tp_mult = float(vol_high_atr_tp_mult) if vol_high_atr_tp_mult is not None else None
        self.vol_low_atr_sl_mult = float(vol_low_atr_sl_mult) if vol_low_atr_sl_mult is not None else None
        self.vol_low_atr_tp_mult = float(vol_low_atr_tp_mult) if vol_low_atr_tp_mult is not None else None
        self.debug_log_hits = bool(debug_log_hits)
        self._block_until: int = 0
        self._debug_max = 0.0
        self._debug_none = 0
        # Trend adjustments
        self.trend_up_prob_delta = trend_up_prob_delta
        self.trend_down_prob_delta = trend_down_prob_delta
        self.trend_chop_prob_delta = trend_chop_prob_delta
        self.trend_up_short_prob_delta = trend_up_short_prob_delta
        self.trend_down_short_prob_delta = trend_down_short_prob_delta
        self.trend_chop_short_prob_delta = trend_chop_short_prob_delta
        self.trend_up_size_mult = trend_up_size_mult
        self.trend_down_size_mult = trend_down_size_mult
        self.trend_chop_size_mult = trend_chop_size_mult
        self.trend_up_short_size_mult = trend_up_short_size_mult
        self.trend_down_short_size_mult = trend_down_short_size_mult
        self.trend_chop_short_size_mult = trend_chop_short_size_mult
        self.trend_up_cooldown = int(max(0, trend_up_cooldown)) if trend_up_cooldown is not None else None
        self.trend_down_cooldown = int(max(0, trend_down_cooldown)) if trend_down_cooldown is not None else None
        self.trend_chop_cooldown = int(max(0, trend_chop_cooldown)) if trend_chop_cooldown is not None else None
        self.trend_up_short_cooldown = int(max(0, trend_up_short_cooldown)) if trend_up_short_cooldown is not None else None
        self.trend_down_short_cooldown = int(max(0, trend_down_short_cooldown)) if trend_down_short_cooldown is not None else None
        self.trend_chop_short_cooldown = int(max(0, trend_chop_short_cooldown)) if trend_chop_short_cooldown is not None else None
        self.trend_up_atr_sl_mult = float(trend_up_atr_sl_mult) if trend_up_atr_sl_mult is not None else None
        self.trend_up_atr_tp_mult = float(trend_up_atr_tp_mult) if trend_up_atr_tp_mult is not None else None
        self.trend_down_atr_sl_mult = float(trend_down_atr_sl_mult) if trend_down_atr_sl_mult is not None else None
        self.trend_down_atr_tp_mult = float(trend_down_atr_tp_mult) if trend_down_atr_tp_mult is not None else None
        self.trend_chop_atr_sl_mult = float(trend_chop_atr_sl_mult) if trend_chop_atr_sl_mult is not None else None
        self.trend_chop_atr_tp_mult = float(trend_chop_atr_tp_mult) if trend_chop_atr_tp_mult is not None else None
        # Session filter (list of [start_hour, end_hour) to block entries)
        self.session_block_hours = []
        for rng in session_block_hours or []:
            try:
                start, end = float(rng[0]), float(rng[1])
                self.session_block_hours.append((start, end))
            except Exception:
                continue
        # Strategy-level regime filter (vol/trend)
        regime_filter = regime_filter or {}
        self.regime_filter_vol = {str(v).lower() for v in regime_filter.get("vol", [])} if isinstance(regime_filter, dict) else set()
        self.regime_filter_trend = {str(v).lower() for v in regime_filter.get("trend", [])} if isinstance(regime_filter, dict) else set()

    def _note_feature_miss(self, reason: str) -> None:
        self._debug_none += 1
        if self.debug_log_hits and self._debug_none <= 10:
            logger.warning(f"[xgb_signal] feature unavailable ({reason})")

    def _short_allowed(self, state: Dict[str, Any]) -> bool:
        if not self.enable_short:
            return False
        if not self.allow_short_regimes:
            return True
        sma_fast = state.get("sma_fast")
        sma_slow = state.get("sma_slow")
        vol_regime = (state.get("vol_regime") or "").lower()
        for cond in self.allow_short_regimes:
            if cond == "trend_down":
                if sma_fast is not None and sma_slow is not None and sma_fast < sma_slow:
                    return True
            if cond == "vol_high" and vol_regime == "high":
                return True
        return False

    def _features_from_state(self, state: Dict[str, Any]) -> tuple[Optional[np.ndarray], Optional[float]]:
        # Close history for returns/volatility
        ch = state.get("close_history")
        if ch is None:
            self._note_feature_miss("close_history missing")
            return None, None
        closes = np.asarray(ch, dtype=float)
        if closes.size < 80:  # need at least slow window context
            self._note_feature_miss("insufficient history")
            return None, None
        close = float(state.get("close", closes[-1]))

        # Returns & rolling vol
        def pct_change(arr: np.ndarray, k: int) -> float | None:
            if arr.size <= k:
                self._note_feature_miss(f"ret_{k} insufficient")
                return None
            a, b = arr[-k - 1], arr[-1]
            return (b - a) / a if a else None

        ret_1 = pct_change(closes, 1)
        ret_3 = pct_change(closes, 3)
        ret_6 = pct_change(closes, 6)
        ret_12 = pct_change(closes, 12) if "ret_12" in self.feature_list else None
        vol_24 = None
        if closes.size >= 25:
            rets = np.diff(closes[-25:]) / closes[-25:-1]
            vol_24 = float(np.std(rets)) if rets.size else None
        vol_48 = None
        if "vol_48" in self.feature_list:
            if closes.size >= 49:
                rets48 = np.diff(closes[-49:]) / closes[-49:-1]
                vol_48 = float(np.std(rets48)) if rets48.size else None
            else:
                self._note_feature_miss("vol_48 insufficient")
                return None, None

        # SMA diff
        sma_fast = _safe_get(state, "sma_fast")
        sma_slow = _safe_get(state, "sma_slow")
        if sma_fast is None or sma_slow is None:
            sma_fast = float(np.mean(closes[-20:])) if closes.size >= 20 else None
            sma_slow = float(np.mean(closes[-80:])) if closes.size >= 80 else None
        if sma_fast is None or sma_slow is None:
            self._note_feature_miss("sma missing")
            return None, None
        sma_diff = (float(sma_fast) - float(sma_slow)) / close if close else 0.0
        sma_diff_14_56 = None
        if "sma_diff_14_56" in self.feature_list:
            if closes.size >= 56:
                sma14 = float(np.mean(closes[-14:]))
                sma56 = float(np.mean(closes[-56:]))
                sma_diff_14_56 = (sma14 - sma56) / close if close else 0.0
            else:
                self._note_feature_miss("sma_diff_14_56 insufficient")
                return None, None

        # RSI (prefer engine state, else compute)
        rsi_val = state.get("rsi")
        if rsi_val is None:
            r = _rsi_from_series(closes, 14)
            rsi_val = r if r is not None else 50.0

        # ATR normalized
        curr_atr = state.get("curr_atr")
        atr_norm = float(curr_atr) / close if (curr_atr is not None and close) else 0.0

        # Time features from ts
        ts = state.get("ts")
        if ts is None:
            self._note_feature_miss("timestamp missing")
            return None, None
        try:
            import pandas as pd
            ts_pd = pd.Timestamp(ts)
            hour = float(ts_pd.hour)
            dow = float(ts_pd.dayofweek)
        except Exception:
            self._note_feature_miss("timestamp parse")
            return None, None
        hour_sin, hour_cos = np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0)
        dow_sin, dow_cos = np.sin(2 * np.pi * dow / 7.0), np.cos(2 * np.pi * dow / 7.0)

        feat_map = {
            "ret_1": ret_1,
            "ret_3": ret_3,
            "ret_6": ret_6,
            "vol_24": vol_24,
            "sma_diff": sma_diff,
            "rsi": float(rsi_val),
            "atr_norm": atr_norm,
            "hour_sin": float(hour_sin),
            "hour_cos": float(hour_cos),
            "dow_sin": float(dow_sin),
            "dow_cos": float(dow_cos),
        }
        if "ret_12" in self.feature_list:
            feat_map["ret_12"] = ret_12
        if "vol_48" in self.feature_list:
            feat_map["vol_48"] = vol_48
        if "sma_diff_14_56" in self.feature_list:
            feat_map["sma_diff_14_56"] = sma_diff_14_56
        vec = []
        for name in self.feature_list:
            val = feat_map.get(name)
            if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
                self._note_feature_miss(f"feature {name} invalid")
                return None, None
            vec.append(float(val))
        return np.asarray(vec, dtype=float), float(vol_24) if vol_24 is not None else None

    def on_bar(self, state: Dict[str, Any]) -> Dict[str, Any]:
        bar_idx = int(state.get("bar_idx", 0) or 0)
        position_units = float(state.get("position_units", 0.0) or 0.0)
        default_qty = float(state.get("default_qty", 0.0) or 0.0)
        close = float(state.get("close", 0.0) or 0.0)

        atr_pct = state.get("atr_percentile")
        if self.min_atr_pct is not None:
            if atr_pct is None or float(atr_pct) < self.min_atr_pct:
                return {"action": "HOLD"}

        # Session filter (clock hours)
        ts = state.get("ts")
        if self.session_block_hours and ts is not None:
            try:
                hour = float(pd.Timestamp(ts).hour)
                for start, end in self.session_block_hours:
                    if start < end:
                        if start <= hour < end:
                            return {"action": "HOLD"}
                    else:  # overnight window
                        if hour >= start or hour < end:
                            return {"action": "HOLD"}
            except Exception:
                pass

        # Strategy-side regime filter
        vol_regime = state.get("vol_regime")
        trend_regime = state.get("trend_regime")
        if self.regime_filter_vol:
            if vol_regime is None or str(vol_regime).lower() not in self.regime_filter_vol:
                return {"action": "HOLD"}
        if self.regime_filter_trend:
            if trend_regime is None or str(trend_regime).lower() not in self.regime_filter_trend:
                return {"action": "HOLD"}

        # Determine per-bar entry threshold / cooldown after ATR gating
        effective_prob_long = self.p_long
        effective_cooldown = self.cooldown_bars
        effective_size_mult = self.size_mult
        effective_atr_sl_mult = 1.0
        effective_atr_tp_mult = 1.0
        if (
            self.low_atr_pct is not None
            and atr_pct is not None
            and float(atr_pct) < self.low_atr_pct
        ):
            if self.prob_long_low is not None:
                effective_prob_long = self.prob_long_low
            if self.cooldown_low is not None:
                effective_cooldown = self.cooldown_low
        if (
            self.atr_relax_pct is not None
            and atr_pct is not None
            and float(atr_pct) >= self.atr_relax_pct
        ):
            if self.p_long_relaxed is not None:
                effective_prob_long = self.p_long_relaxed
            if self.cooldown_relaxed is not None:
                effective_cooldown = self.cooldown_relaxed

        effective_prob_short = self.p_short
        effective_short_size = self.short_size_mult
        effective_short_cooldown = self.short_cooldown_bars if self.short_cooldown_bars is not None else self.cooldown_bars
        if vol_regime == "high":
            if self.vol_high_prob_delta is not None:
                effective_prob_long = max(0.0, min(1.0, effective_prob_long + self.vol_high_prob_delta))
            if self.vol_high_size_mult is not None:
                effective_size_mult = self.vol_high_size_mult
            if self.vol_high_cooldown is not None:
                effective_cooldown = self.vol_high_cooldown
            if self.vol_high_atr_sl_mult is not None:
                effective_atr_sl_mult = self.vol_high_atr_sl_mult
            if self.vol_high_atr_tp_mult is not None:
                effective_atr_tp_mult = self.vol_high_atr_tp_mult
            if self.short_vol_high_prob_delta is not None and effective_prob_short is not None:
                effective_prob_short = max(0.0, min(1.0, effective_prob_short + self.short_vol_high_prob_delta))
            if self.short_vol_high_size_mult is not None:
                effective_short_size = self.short_vol_high_size_mult
            if self.short_vol_high_cooldown is not None:
                effective_short_cooldown = self.short_vol_high_cooldown
        elif vol_regime == "low":
            if self.vol_low_prob_delta is not None:
                effective_prob_long = max(0.0, min(1.0, effective_prob_long + self.vol_low_prob_delta))
            if self.vol_low_size_mult is not None:
                effective_size_mult = self.vol_low_size_mult
            if self.vol_low_cooldown is not None:
                effective_cooldown = self.vol_low_cooldown
            if self.vol_low_atr_sl_mult is not None:
                effective_atr_sl_mult = self.vol_low_atr_sl_mult
            if self.vol_low_atr_tp_mult is not None:
                effective_atr_tp_mult = self.vol_low_atr_tp_mult
            if self.short_vol_low_prob_delta is not None and effective_prob_short is not None:
                effective_prob_short = max(0.0, min(1.0, effective_prob_short + self.short_vol_low_prob_delta))
            if self.short_vol_low_size_mult is not None:
                effective_short_size = self.short_vol_low_size_mult
            if self.short_vol_low_cooldown is not None:
                effective_short_cooldown = self.short_vol_low_cooldown

        # Trend gating (optional)
        trend_regime = self._predict_trend(state) if self.trend_model else None
        if trend_regime == "trend_up":
            if self.trend_up_prob_delta is not None:
                effective_prob_long = max(0.0, min(1.0, effective_prob_long + self.trend_up_prob_delta))
            if self.trend_up_size_mult is not None:
                effective_size_mult = self.trend_up_size_mult
            if self.trend_up_cooldown is not None:
                effective_cooldown = self.trend_up_cooldown
            if self.trend_up_atr_sl_mult is not None:
                effective_atr_sl_mult = self.trend_up_atr_sl_mult
            if self.trend_up_atr_tp_mult is not None:
                effective_atr_tp_mult = self.trend_up_atr_tp_mult
            if self.trend_up_short_prob_delta is not None and effective_prob_short is not None:
                effective_prob_short = max(0.0, min(1.0, effective_prob_short + self.trend_up_short_prob_delta))
            if self.trend_up_short_size_mult is not None:
                effective_short_size = self.trend_up_short_size_mult
            if self.trend_up_short_cooldown is not None:
                effective_short_cooldown = self.trend_up_short_cooldown
        elif trend_regime == "trend_down":
            if self.trend_down_prob_delta is not None:
                effective_prob_long = max(0.0, min(1.0, effective_prob_long + self.trend_down_prob_delta))
            if self.trend_down_size_mult is not None:
                effective_size_mult = self.trend_down_size_mult
            if self.trend_down_cooldown is not None:
                effective_cooldown = self.trend_down_cooldown
            if self.trend_down_atr_sl_mult is not None:
                effective_atr_sl_mult = self.trend_down_atr_sl_mult
            if self.trend_down_atr_tp_mult is not None:
                effective_atr_tp_mult = self.trend_down_atr_tp_mult
            if self.trend_down_short_prob_delta is not None and effective_prob_short is not None:
                effective_prob_short = max(0.0, min(1.0, effective_prob_short + self.trend_down_short_prob_delta))
            if self.trend_down_short_size_mult is not None:
                effective_short_size = self.trend_down_short_size_mult
            if self.trend_down_short_cooldown is not None:
                effective_short_cooldown = self.trend_down_short_cooldown
        elif trend_regime == "chop":
            if self.trend_chop_prob_delta is not None:
                effective_prob_long = max(0.0, min(1.0, effective_prob_long + self.trend_chop_prob_delta))
            if self.trend_chop_size_mult is not None:
                effective_size_mult = self.trend_chop_size_mult
            if self.trend_chop_cooldown is not None:
                effective_cooldown = self.trend_chop_cooldown
            if self.trend_chop_atr_sl_mult is not None:
                effective_atr_sl_mult = self.trend_chop_atr_sl_mult
            if self.trend_chop_atr_tp_mult is not None:
                effective_atr_tp_mult = self.trend_chop_atr_tp_mult
            if self.trend_chop_short_prob_delta is not None and effective_prob_short is not None:
                effective_prob_short = max(0.0, min(1.0, effective_prob_short + self.trend_chop_short_prob_delta))
            if self.trend_chop_short_size_mult is not None:
                effective_short_size = self.trend_chop_short_size_mult
            if self.trend_chop_short_cooldown is not None:
                effective_short_cooldown = self.trend_chop_short_cooldown

        # Cooldown gate (updated per bar)
        if bar_idx < self._block_until:
            return {"action": "HOLD"}

        feats, vol_24 = self._features_from_state(state)
        if feats is None:
            return {"action": "HOLD"}
        if self.min_vol_24 is not None:
            if vol_24 is None or vol_24 < self.min_vol_24:
                return {"action": "HOLD"}

        dmat = self._xgb.DMatrix(feats.reshape(1, -1), feature_names=self.feature_list)
        pred = self._booster.predict(dmat)
        if self.label_mode == "multi":
            prob_long = float(pred[0][2])
            prob_short_val = float(pred[0][0])
        else:
            prob_long = float(pred[0])
            prob_short_val = None
        p_up = prob_long
        if p_up > self._debug_max:
            self._debug_max = p_up
            if self.debug_log_hits:
                logger.info(
                    "[xgb_signal] new max prob %.4f (ts=%s close=%.5f position=%s)",
                    p_up,
                    state.get("ts"),
                    close,
                    position_units,
                )

        short_allowed = self._short_allowed(state)

        if position_units == 0.0:
            if self.enable_short and short_allowed and prob_short_val is not None and effective_prob_short is not None and prob_short_val >= effective_prob_short and default_qty > 0.0:
                self._block_until = bar_idx + effective_short_cooldown
                if self.debug_log_hits:
                    logger.info(
                        "[xgb_signal] ENTER SHORT p=%.4f (thr=%.4f) ts=%s",
                        prob_short_val,
                        effective_prob_short,
                        state.get("ts"),
                    )
                return {
                    "action": "ENTER_SHORT",
                    "size": default_qty * effective_short_size,
                    "atr_sl_mult": effective_atr_sl_mult,
                    "atr_tp_mult": effective_atr_tp_mult,
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                }
            if p_up >= effective_prob_long and default_qty > 0.0:
                self._block_until = bar_idx + effective_cooldown
                if self.debug_log_hits:
                    logger.info(
                        "[xgb_signal] ENTER signal p=%.4f (thr=%.4f) ts=%s",
                        p_up,
                        effective_prob_long,
                        state.get("ts"),
                    )
                return {
                    "action": "ENTER_LONG",
                    "size": default_qty * effective_size_mult,
                    "atr_sl_mult": effective_atr_sl_mult,
                    "atr_tp_mult": effective_atr_tp_mult,
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                }
            return {"action": "HOLD"}
        elif position_units > 0:
            if p_up < self.p_exit:
                if self.debug_log_hits:
                    logger.info(
                        "[xgb_signal] EXIT signal p=%.4f (thr=%.4f) ts=%s",
                        p_up,
                        self.p_exit,
                        state.get("ts"),
                    )
                return {"action": "EXIT_LONG"}
            return {"action": "HOLD"}
        else:  # short position
            exit_cond = False
            if not self.enable_short:
                exit_cond = True
            elif prob_short_val is None or self.p_short_exit is None:
                exit_cond = True
            else:
                if prob_short_val <= self.p_short_exit:
                    exit_cond = True
                if not short_allowed:
                    exit_cond = True
            if exit_cond:
                if self.debug_log_hits:
                    logger.info(
                        "[xgb_signal] EXIT SHORT p=%.4f (thr=%.4f) ts=%s",
                        prob_short_val if prob_short_val is not None else -1,
                        self.p_short_exit if self.p_short_exit is not None else -1,
                        state.get("ts"),
                    )
                return {"action": "EXIT_SHORT"}
            return {"action": "HOLD"}

    def _predict_trend(self, state: Dict[str, Any]) -> Optional[str]:
        if not self.trend_model or not self.trend_feature_list:
            return None
        feats = self._build_trend_features(state)
        if feats is None:
            return None
        dmat = self._xgb.DMatrix(feats.reshape(1, -1), feature_names=self.trend_feature_list)
        proba = self.trend_model.predict(dmat).reshape(-1, 3)[0]
        idx = int(np.argmax(proba))
        for name, cid in self.trend_mapping.items():
            if cid == idx:
                return name
        return None

    def _build_trend_features(self, state: Dict[str, Any]) -> Optional[np.ndarray]:
        closes = state.get("close_history")
        highs = state.get("high_history")
        lows = state.get("low_history")
        if closes is None or highs is None or lows is None:
            return None
        closes = np.asarray(closes, dtype=float)
        highs = np.asarray(highs, dtype=float)
        lows = np.asarray(lows, dtype=float)
        if min(closes.size, highs.size, lows.size) < 80:
            return None
        close = closes[-1]

        def pct(arr: np.ndarray, k: int) -> Optional[float]:
            if arr.size <= k or arr[-k - 1] == 0:
                return None
            return (arr[-1] - arr[-k - 1]) / arr[-k - 1]

        def roll_vol(arr: np.ndarray, window: int) -> Optional[float]:
            if arr.size <= window:
                return None
            rets = np.diff(arr[-(window + 1) :]) / arr[-(window + 1) : -1]
            return float(np.std(rets)) if rets.size else None

        sma_fast = float(np.mean(closes[-20:])) if closes.size >= 20 else None
        sma_slow = float(np.mean(closes[-80:])) if closes.size >= 80 else None
        ema20_series = pd.Series(closes).ewm(span=20, adjust=False).mean()
        ema80_series = pd.Series(closes).ewm(span=80, adjust=False).mean()
        ema_diff = (ema20_series.iloc[-1] - ema80_series.iloc[-1]) / close if close else None
        def slope(series: pd.Series, lookback: int = 5) -> Optional[float]:
            if len(series) <= lookback:
                return None
            return float(series.iloc[-1] - series.iloc[-lookback])
        ema_slope_20 = slope(ema20_series, 5)
        ema_slope_80 = slope(ema80_series, 5)
        rsi_val = _rsi_from_series(closes, 14) or 50.0
        atr14 = self._atr_from_state(highs, lows, closes, 14)
        atr50 = self._atr_from_state(highs, lows, closes, 50)

        ts = state.get("ts")
        try:
            ts_pd = pd.Timestamp(ts)
            hour = float(ts_pd.hour)
            dow = float(ts_pd.dayofweek)
            hour_sin, hour_cos = np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0)
            dow_sin, dow_cos = np.sin(2 * np.pi * dow / 7.0), np.cos(2 * np.pi * dow / 7.0)
        except Exception:
            return None

        feat_map = {
            "ret_1": pct(closes, 1),
            "ret_3": pct(closes, 3),
            "ret_6": pct(closes, 6),
            "ret_12": pct(closes, 12),
            "vol_24": roll_vol(closes, 24),
            "vol_48": roll_vol(closes, 48),
            "sma_diff": ((sma_fast - sma_slow) / close) if (sma_fast is not None and sma_slow is not None and close) else None,
            "ema_diff": ema_diff,
            "ema_slope_20": ema_slope_20,
            "ema_slope_80": ema_slope_80,
            "rsi": rsi_val,
            "atr_norm_14": (atr14 / close) if (atr14 is not None and close) else None,
            "atr_norm_50": (atr50 / close) if (atr50 is not None and close) else None,
            "hour_sin": hour_sin,
            "hour_cos": hour_cos,
            "dow_sin": dow_sin,
            "dow_cos": dow_cos,
        }
        row = []
        for f in self.trend_feature_list:
            val = feat_map.get(f)
            if val is None:
                return None
            row.append(float(val))
        return np.asarray(row, dtype=float)

    def _atr_from_state(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int) -> Optional[float]:
        if min(highs.size, lows.size, closes.size) < period + 1:
            return None
        prev_close = closes[-period - 1 : -1]
        tr = np.maximum.reduce(
            [
                highs[-period:] - lows[-period:],
                np.abs(highs[-period:] - prev_close),
                np.abs(lows[-period:] - prev_close),
            ]
        )
        return float(np.mean(tr))
