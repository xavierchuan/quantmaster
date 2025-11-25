#!/usr/bin/env python3
"""
Generic regime labeling for FX H1 clean data.

Usage example:
  python QuantResearch/scripts/label_regimes_fx.py \
    --base-csv QuantResearch/data/clean/EURUSD_H1_clean_v2.csv \
    --out-csv QuantResearch/data/clean/EURUSD_H1_clean_v2_with_regime.csv \
    --vol-latest QuantResearch/artifacts/models/eurusd_vol_regime_latest.json \
    --trend-latest QuantResearch/artifacts/models/eurusd_trend_regime_latest.json

Notes:
  - Expects base CSV to have columns: time, open, high, low, close, volume.
  - Uses build_features from train_vol_regime_usdjpy.py and train_trend_regime_usdjpy.py
    (feature logic is shared).
  - Latest pointers must contain "model_dir" pointing to a dir with model.json (+ feature files).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    import xgboost as xgb  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("xgboost is required: pip install xgboost==1.7.6") from exc

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.append(str(RESEARCH_ROOT))


def load_latest(ptr_path: Path) -> Path:
    payload = json.loads(ptr_path.read_text())
    model_dir = payload.get("model_dir")
    if not model_dir:
        raise RuntimeError(f"{ptr_path} missing model_dir")
    full = Path(model_dir)
    if not full.exists():
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


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Label vol_regime / trend_regime for FX H1 clean CSV.")
    ap.add_argument("--base-csv", required=True, type=Path, help="Clean H1 CSV (time/open/high/low/close/volume).")
    ap.add_argument("--out-csv", required=True, type=Path, help="Output CSV with regime columns appended.")
    ap.add_argument("--vol-latest", required=True, type=Path, help="Pointer JSON to latest vol model dir.")
    ap.add_argument("--trend-latest", required=True, type=Path, help="Pointer JSON to latest trend model dir.")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    if not args.base_csv.exists():
        raise SystemExit(f"Base clean CSV not found: {args.base_csv}")
    df_base = pd.read_csv(args.base_csv)
    if "time" not in df_base.columns:
        raise SystemExit(f"'time' column missing in {args.base_csv}")

    # Vol regime
    vol_dir = load_latest(args.vol_latest)
    vol_feat_df, vol_feat_list = _build_vol_features(df_base[["time", "open", "high", "low", "close", "volume"]])
    vol_model_path = vol_dir / "model.json"
    vol_meta_path = vol_dir / "meta.json"
    vol_meta = json.loads(vol_meta_path.read_text()) if vol_meta_path.exists() else {}
    vol_class_mapping = vol_meta.get("class_mapping", {"vol_low": 0, "vol_normal": 1, "vol_high": 2})
    vol_feature_list = vol_meta.get("features", vol_feat_list)
    vol_labels, _ = predict_labels(vol_feat_df, vol_feature_list, vol_model_path, vol_class_mapping)
    df_base["vol_regime"] = vol_labels.values
    df_base["vol_high"] = df_base["vol_regime"] == "vol_high"
    df_base["vol_low"] = df_base["vol_regime"] == "vol_low"

    # Trend regime
    trend_dir = load_latest(args.trend_latest)
    trend_feat_df, trend_feat_list = _build_trend_features(df_base[["time", "open", "high", "low", "close", "volume"]])
    trend_model_path = trend_dir / "model.json"
    trend_thresholds = json.loads((trend_dir / "thresholds.json").read_text()) if (trend_dir / "thresholds.json").exists() else {}
    trend_class_mapping = trend_thresholds.get("class_mapping", {"trend_down": 0, "chop": 1, "trend_up": 2})
    trend_feature_list = json.loads((trend_dir / "feature_list.json").read_text()) if (trend_dir / "feature_list.json").exists() else trend_feat_list
    trend_labels, _ = predict_labels(trend_feat_df, trend_feature_list, trend_model_path, trend_class_mapping)
    df_base["trend_regime"] = trend_labels.values

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    df_base.to_csv(args.out_csv, index=False)
    print(f"[regime] wrote {args.out_csv} with vol_regime/vol_high/vol_low/trend_regime")


if __name__ == "__main__":
    main()
