"""Volatility regime classifier strategy (state writer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from . import register
from .base import Strategy


def _load_latest_ptr(latest_ptr: str) -> Path:
    ptr = Path(latest_ptr)
    if not ptr.exists():
        raise RuntimeError(f"volatility regime latest pointer not found: {ptr}")
    data = json.loads(ptr.read_text(encoding="utf-8"))
    model_dir = data.get("model_dir")
    if not model_dir:
        raise RuntimeError(f"latest ptr missing model_dir: {ptr}")
    return Path(model_dir)


@register("regime_vol_ml")
class VolRegimeML(Strategy):
    """
    Predicts volatility regime (low/normal/high) via XGBoost and writes results into state.

    This strategy never places orders; downstream strategies read:
        state["vol_regime"] -> str ("low"/"normal"/"high")
        state["vol_regime_proba"] -> List[float] length 3
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        latest_ptr: str = "QuantResearch/artifacts/models/usdjpy_vol_regime_latest.json",
        min_confidence: float = 0.0,
        debug: bool = False,
    ) -> None:
        super().__init__()
        try:
            import xgboost as xgb  # type: ignore
        except Exception as exc:
            raise RuntimeError("regime_vol_ml requires xgboost. Install xgboost==1.7.6") from exc
        self._xgb = xgb
        self._available = True
        try:
            if not model_dir:
                model_path = _load_latest_ptr(latest_ptr)
            else:
                model_path = Path(model_dir)
            self.model_dir = model_path
            feature_file = model_path / "feature_list.json"
            if not feature_file.exists():
                raise RuntimeError(f"feature_list.json not found in {model_path}")
            self.feature_list: List[str] = json.loads(feature_file.read_text(encoding="utf-8"))
            self._booster = xgb.Booster()
            self._booster.load_model(str(model_path / "model.json"))
        except Exception as exc:
            logger.warning(f"[regime_vol_ml] failed to load model: {exc}. Running in no-op mode.")
            self._available = False
            self.feature_list = []
            self._booster = None
        self.min_confidence = float(min_confidence)
        self.debug = bool(debug)
        self._labels = ["low", "normal", "high"]

    def _feature_from_state(self, state: Dict[str, Any]) -> Optional[np.ndarray]:
        closes = state.get("close_history")
        if closes is None or len(closes) < 90:
            return None
        closes = np.asarray(closes, dtype=float)
        close = closes[-1]

        def pct_change(k: int) -> Optional[float]:
            if len(closes) <= k:
                return None
            prev = closes[-k - 1]
            if prev == 0:
                return None
            return (close - prev) / prev

        feats = {
            "ret_1": pct_change(1),
            "ret_3": pct_change(3),
            "ret_6": pct_change(6),
            "ret_24": pct_change(24),
        }
        if len(closes) >= 25:
            rets = np.diff(closes[-25:]) / closes[-25:-1]
            feats["realized_vol_10"] = float(np.std(rets[-10:])) if rets.size >= 10 else float(np.std(rets)) if rets.size else None
        else:
            feats["realized_vol_10"] = None

        sma_fast = state.get("sma_fast")
        sma_slow = state.get("sma_slow")
        if sma_fast is not None and sma_slow is not None and close != 0:
            feats["sma_diff"] = (float(sma_fast) - float(sma_slow)) / close
        else:
            feats["sma_diff"] = None

        curr_atr = state.get("curr_atr")
        feats["atr_norm"] = (float(curr_atr) / close) if (curr_atr is not None and close) else None
        feats["atr_percentile"] = state.get("atr_percentile")

        ts = state.get("ts")
        if ts is None:
            return None
        try:
            import pandas as pd

            ts_pd = pd.Timestamp(ts, tz="UTC")
        except Exception:
            return None
        hour = float(ts_pd.hour)
        dow = float(ts_pd.dayofweek)
        feats["hour_sin"] = float(np.sin(2 * np.pi * hour / 24.0))
        feats["hour_cos"] = float(np.cos(2 * np.pi * hour / 24.0))
        feats["dow_sin"] = float(np.sin(2 * np.pi * dow / 7.0))
        feats["dow_cos"] = float(np.cos(2 * np.pi * dow / 7.0))

        values: List[float] = []
        for name in self.feature_list:
            val = feats.get(name)
            if val is None or np.isnan(val):
                return None
            values.append(float(val))
        return np.asarray(values, dtype=float)

    def on_bar(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not self._available or self._booster is None:
            return {"action": "HOLD"}
        vec = self._feature_from_state(state)
        if vec is None:
            return {"action": "HOLD"}
        dmat = self._xgb.DMatrix(vec.reshape(1, -1), feature_names=self.feature_list)
        proba = self._booster.predict(dmat).reshape(-1)
        idx = int(np.argmax(proba))
        confidence = float(np.max(proba))
        if confidence < self.min_confidence:
            label = "normal"
        else:
            label = self._labels[idx]
        state["vol_regime"] = label
        state["vol_regime_proba"] = proba.tolist()
        state["vol_regime_confidence"] = confidence
        if self.debug:
            print(f"[regime_vol_ml] ts={state.get('ts')} label={label} p={proba.round(3)}")
        return {"action": "HOLD"}
