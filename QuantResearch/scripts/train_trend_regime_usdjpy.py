#!/usr/bin/env python3
"""
Train a trend regime classifier (trend_up / chop / trend_down) on USDJPY H1 data.

Artifacts are stored under QuantResearch/artifacts/models/{symbol_lower}_trend_regime/<ts>/
  - model.json
  - feature_list.json
  - thresholds.json (class mapping + metrics)
  - meta.json

Pointer file: QuantResearch/artifacts/models/{symbol_lower}_trend_regime_latest.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train trend regime classifier on FX H1")
    p.add_argument("--csv", default="QuantResearch/data/clean/USDJPY_H1_clean_v2.csv")
    p.add_argument("--symbol", default="USDJPY")
    p.add_argument("--horizon", type=int, default=12, help="forward return horizon bars")
    p.add_argument("--pos-thresh", type=float, default=0.0005, help="forward return threshold for trend_up")
    p.add_argument("--neg-thresh", type=float, default=-0.0005, help="forward return threshold for trend_down")
    p.add_argument("--train-ratio", type=float, default=0.6)
    p.add_argument("--val-ratio", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default=None, help="Output dir (default: artifacts/models/{symbol_lower}_trend_regime)")
    p.add_argument("--latest-ptr", default=None, help="Pointer file (default: artifacts/models/{symbol_lower}_trend_regime_latest.json)")
    p.add_argument("--n-estimators", type=int, default=300)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--learning-rate", type=float, default=0.05)
    return p.parse_args()


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["time"], utc=True)
    hour = ts.dt.hour.astype(float)
    dow = ts.dt.dayofweek.astype(float)
    df["hour_sin"], df["hour_cos"] = np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0)
    df["dow_sin"], df["dow_cos"] = np.sin(2 * np.pi * dow / 7.0), np.cos(2 * np.pi * dow / 7.0)
    return df


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    out = add_time_features(df.copy())
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_3"] = out["close"].pct_change(3)
    out["ret_6"] = out["close"].pct_change(6)
    out["ret_12"] = out["close"].pct_change(12)
    out["vol_24"] = out["close"].pct_change(1).rolling(24).std()
    out["vol_48"] = out["close"].pct_change(1).rolling(48).std()
    sma_fast = out["close"].rolling(20).mean()
    sma_slow = out["close"].rolling(80).mean()
    out["sma_diff"] = (sma_fast - sma_slow) / out["close"]
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema80"] = out["close"].ewm(span=80, adjust=False).mean()
    out["ema_diff"] = (out["ema20"] - out["ema80"]) / out["close"]
    out["ema_slope_20"] = out["ema20"].diff(5)
    out["ema_slope_80"] = out["ema80"].diff(5)
    out["rsi"] = rsi(out["close"], 14)
    out["atr_norm_14"] = atr(out["high"], out["low"], out["close"], 14) / out["close"]
    out["atr_norm_50"] = atr(out["high"], out["low"], out["close"], 50) / out["close"]
    feats = [
        "ret_1", "ret_3", "ret_6", "ret_12",
        "vol_24", "vol_48",
        "sma_diff", "ema_diff", "ema_slope_20", "ema_slope_80",
        "rsi", "atr_norm_14", "atr_norm_50",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    return out, feats


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(period).mean()
    roll_down = down.rolling(period).mean()
    rs = roll_up / roll_down
    return 100 - (100 / (1 + rs))


def label_trend(close: pd.Series, horizon: int, pos_thr: float, neg_thr: float) -> pd.Series:
    fwd = close.shift(-horizon) / close - 1.0
    labels = pd.Series(np.nan, index=close.index)
    labels.loc[fwd >= pos_thr] = 2  # trend_up
    labels.loc[(fwd > neg_thr) & (fwd < pos_thr)] = 1  # chop
    labels.loc[fwd <= neg_thr] = 0  # trend_down
    return labels


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, num_class: int = 3) -> List[List[int]]:
    mat = np.zeros((num_class, num_class), dtype=int)
    for yt, yp in zip(y_true, y_pred):
        mat[int(yt), int(yp)] += 1
    return mat.tolist()


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def main() -> None:
    try:
        import xgboost as xgb  # type: ignore
    except Exception as exc:
        raise SystemExit("xgboost is required (pip install xgboost==1.7.6).") from exc

    args = parse_args()
    np.random.seed(args.seed)

    symbol_lower = str(args.symbol).lower()
    default_out = Path(f"QuantResearch/artifacts/models/{symbol_lower}_trend_regime")
    default_ptr = Path(f"QuantResearch/artifacts/models/{symbol_lower}_trend_regime_latest.json")
    out_dir_root = Path(args.out) if args.out else default_out
    latest_ptr_path = Path(args.latest_ptr) if args.latest_ptr else default_ptr

    df = pd.read_csv(args.csv)
    required = {"time", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        raise SystemExit(f"CSV must contain columns: {required}")
    df = df[list(required)].copy()

    df_feat, feat_list = build_features(df)
    labels = label_trend(df_feat["close"], args.horizon, args.pos_thresh, args.neg_thresh)

    data = df_feat.assign(y=labels).dropna(subset=feat_list + ["y"]).reset_index(drop=True)
    X = data[feat_list].to_numpy(dtype=float)
    y = data["y"].astype(int).to_numpy()

    n = len(data)
    i_train = int(n * args.train_ratio)
    i_val = int(n * (args.train_ratio + args.val_ratio))
    if i_val >= n:
        i_val = n - max(1, n // 10)

    X_train, y_train = X[:i_train], y[:i_train]
    X_val, y_val = X[i_train:i_val], y[i_train:i_val]
    X_test, y_test = X[i_val:], y[i_val:]

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feat_list)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feat_list)
    dtest = xgb.DMatrix(X_test, label=y_test, feature_names=feat_list)

    params = {
        "objective": "multi:softprob",
        "num_class": 3,
        "eval_metric": "mlogloss",
        "max_depth": args.max_depth,
        "eta": args.learning_rate,
        "lambda": 1.0,
        "min_child_weight": 1.0,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "seed": args.seed,
    }
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=args.n_estimators,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=50,
        verbose_eval=False,
    )

    def classify(dm):
        proba = booster.predict(dm)
        return np.asarray(proba).reshape(-1, 3).argmax(axis=1)

    pred_train = classify(dtrain)
    pred_val = classify(dval)
    pred_test = classify(dtest)

    def acc(y_true, y_pred):
        return float((y_true == y_pred).mean()) if len(y_true) else 0.0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = out_dir_root.with_suffix("") / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    booster.save_model(str(out_dir / "model.json"))
    save_json(out_dir / "feature_list.json", feat_list)

    metrics = {
        "train_acc": acc(y_train, pred_train),
        "val_acc": acc(y_val, pred_val),
        "test_acc": acc(y_test, pred_test),
        "train_confusion": confusion_matrix(y_train, pred_train, 3),
        "val_confusion": confusion_matrix(y_val, pred_val, 3),
        "test_confusion": confusion_matrix(y_test, pred_test, 3),
    }
    save_json(out_dir / "thresholds.json", {"metrics": metrics, "class_mapping": {"trend_down": 0, "chop": 1, "trend_up": 2}})

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol,
        "csv": args.csv,
        "horizon": args.horizon,
        "pos_thresh": args.pos_thresh,
        "neg_thresh": args.neg_thresh,
        "features": feat_list,
        "splits": {"train": len(y_train), "val": len(y_val), "test": len(y_test)},
        "xgb_params": params,
        "best_iteration": int(getattr(booster, "best_iteration", 0) or 0),
        "metrics": metrics,
    }
    save_json(out_dir / "meta.json", meta)

    latest_ptr = latest_ptr_path
    latest_ptr.parent.mkdir(parents=True, exist_ok=True)
    latest_ptr.write_text(json.dumps({"model_dir": str(out_dir), "label_mode": "multi"}, indent=2), encoding="utf-8")
    print(f"Saved trend regime model to {out_dir}")


if __name__ == "__main__":
    main()
