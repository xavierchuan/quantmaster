#!/usr/bin/env python3
"""
Generate a unified clean CSV with vol_regime / trend_regime labels for USDJPY H1.

Uses the latest vol and trend models (pointers in artifacts/..._latest.json),
applies their feature builders, and writes a merged CSV alongside the base clean file.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    import xgboost as xgb  # type: ignore
except Exception as exc:  # pragma: no cover - runtime dependency
    raise SystemExit("xgboost is required: pip install xgboost==1.7.6") from exc

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
BASE_CSV = RESEARCH_ROOT / "data" / "clean" / "USDJPY_H1_clean_v2.csv"
OUT_CSV = RESEARCH_ROOT / "data" / "clean" / "USDJPY_H1_clean_v2_with_regime.csv"
VOL_LATEST = RESEARCH_ROOT / "artifacts" / "models" / "usdjpy_vol_regime_latest.json"
TREND_LATEST = RESEARCH_ROOT / "artifacts" / "models" / "usdjpy_trend_regime_latest.json"

# Allow importing sibling training scripts
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.append(str(RESEARCH_ROOT))


def load_latest(ptr_path: Path) -> Path:
    payload = json.loads(ptr_path.read_text())
    model_dir = payload.get("model_dir")
    if not model_dir:
        raise RuntimeError(f"{ptr_path} missing model_dir")
    full = Path(model_dir)
    if not full.exists():
        # Allow relative path from research root
        full = RESEARCH_ROOT / model_dir
    if not full.exists():
        raise RuntimeError(f"Model dir not found: {full}")
    return full


def _build_vol_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    from scripts.train_vol_regime_usdjpy import build_features

    return build_features(df)


def _build_trend_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    from scripts.train_trend_regime_usdjpy import build_features

    return build_features(df)


def predict_labels(
    feat_df: pd.DataFrame,
    feature_list: List[str],
    model_path: Path,
    class_mapping: Dict[str, int],
) -> Tuple[pd.Series, np.ndarray]:
    """
    Predict class labels (string) and probabilities.
    Returns (label_series, proba_array) aligned to feat_df index.
    """
    booster = xgb.Booster()
    booster.load_model(str(model_path))
    feat_mat = feat_df[feature_list]
    mask = feat_mat.notna().all(axis=1)
    proba = np.full((len(feat_df), len(class_mapping)), np.nan)
    labels = pd.Series(index=feat_df.index, dtype=object)
    if mask.any():
        dm = xgb.DMatrix(feat_mat.loc[mask].to_numpy(dtype=float), feature_names=feature_list)
        pred = booster.predict(dm).reshape(-1, len(class_mapping))
        proba[mask.to_numpy()] = pred
        int_labels = np.argmax(pred, axis=1)
        inv_map = {v: k for k, v in class_mapping.items()}
        labels.loc[mask] = [inv_map.get(int(i), None) for i in int_labels]
    return labels, proba


def main() -> None:
    if not BASE_CSV.exists():
        raise SystemExit(f"Base clean CSV not found: {BASE_CSV}")
    df_base = pd.read_csv(BASE_CSV)
    if "time" not in df_base.columns:
        raise SystemExit(f"'time' column missing in {BASE_CSV}")

    # Vol regime
    vol_dir = load_latest(VOL_LATEST)
    vol_feat_df, vol_feat_list = _build_vol_features(df_base[["time", "open", "high", "low", "close", "volume"]])
    vol_model_path = vol_dir / "model.json"
    vol_meta = json.loads((vol_dir / "meta.json").read_text())
    vol_class_mapping = {"vol_low": 0, "vol_normal": 1, "vol_high": 2}
    vol_labels, _ = predict_labels(vol_feat_df, vol_meta.get("features", vol_feat_list), vol_model_path, vol_class_mapping)
    df_base["vol_regime"] = vol_labels.values
    df_base["vol_high"] = df_base["vol_regime"] == "vol_high"
    df_base["vol_low"] = df_base["vol_regime"] == "vol_low"

    # Trend regime
    trend_dir = load_latest(TREND_LATEST)
    trend_feat_df, trend_feat_list = _build_trend_features(df_base[["time", "open", "high", "low", "close", "volume"]])
    trend_model_path = trend_dir / "model.json"
    trend_thresholds = json.loads((trend_dir / "thresholds.json").read_text())
    trend_class_mapping = trend_thresholds.get("class_mapping", {"trend_down": 0, "chop": 1, "trend_up": 2})
    trend_labels, _ = predict_labels(trend_feat_df, json.loads((trend_dir / "feature_list.json").read_text()), trend_model_path, trend_class_mapping)
    df_base["trend_regime"] = trend_labels.values

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_base.to_csv(OUT_CSV, index=False)
    print(f"[regime] wrote {OUT_CSV} with columns vol_regime/vol_high/vol_low/trend_regime")


if __name__ == "__main__":
    main()
