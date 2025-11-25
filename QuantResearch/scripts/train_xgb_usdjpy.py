#!/usr/bin/env python3
"""
Train an XGBoost classifier on FX H1 and export model artifacts.

Artifacts are written to: QuantResearch/artifacts/models/{symbol_lower}_h1_xgb_v2/<ts>/
  - model.json           (xgboost Booster)
  - feature_list.json    (ordered feature names)
  - thresholds.json      (p_long, p_exit, val/test metrics)
  - meta.json            (dataset/costs/params/seed/etc.)

Also updates: QuantResearch/artifacts/models/{symbol_lower}_h1_xgb_latest.json
  {"model_dir": "QuantResearch/artifacts/models/{symbol_lower}_h1_xgb_v2/<ts>"}
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train XGB (long-only) on FX H1")
    p.add_argument("--csv", default="QuantResearch/data/clean/USDJPY_H1_clean_v2_with_regime.csv")
    p.add_argument("--symbol", default="USDJPY")
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--train-ratio", type=float, default=0.6)
    p.add_argument("--val-ratio", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    # Unified costs (match backtest/YAML)
    p.add_argument("--spread-pips", type=float, default=2.0)
    p.add_argument("--slip-pips", type=float, default=0.3)
    p.add_argument("--comm-per-million", type=float, default=0.25)
    # Model params (conservative defaults)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--n-estimators", type=int, default=300)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--min-child-weight", type=float, default=1.0)
    p.add_argument("--reg-lambda", type=float, default=1.0)
    p.add_argument("--scale-pos-weight", type=float, default=1.0, help="Positive class weight (binary mode)")
    # Output base dir
    p.add_argument("--out", default=None, help="Output dir base (default: artifacts/models/{symbol_lower}_h1_xgb_v2)")
    p.add_argument("--latest-ptr", default=None, help="Pointer file (default: artifacts/models/{symbol_lower}_h1_xgb_latest.json)")
    p.add_argument("--label-mode", choices=["binary", "multi"], default="binary", help="binary=long only, multi=long/flat/short")
    # Optional extra features (for horizontal ensemble variants)
    p.add_argument("--add-ret12", action="store_true", help="Include ret_12 feature")
    p.add_argument("--add-vol48", action="store_true", help="Include vol_48 feature")
    p.add_argument("--add-sma14-56", action="store_true", help="Include sma_diff_14_56 feature")
    # Stress-test / robustness hooks
    p.add_argument("--label-shuffle", type=float, default=0.0, help="Shuffle labels completely (0-1 fraction).")
    p.add_argument("--label-noise", type=float, default=0.0, help="Randomly flip labels with given probability.")
    p.add_argument("--block-shuffle", type=int, default=0, help="Shuffle data in blocks of this many rows.")
    p.add_argument("--drop-regime", type=float, default=0.0, help="Randomly drop this fraction of regime-labelled rows.")
    p.add_argument("--drop-regime-mode", choices=["row", "feature"], default="row", help="row=drop samples, feature=remove regime columns")
    p.add_argument("--feature-mask", default="", help="Comma-separated feature names to remove.")
    p.add_argument("--train-start", help="Filter samples starting from this ISO date (inclusive).")
    p.add_argument("--train-end", help="Filter samples ending at this ISO date (inclusive).")
    p.add_argument("--vol-warp", type=float, default=0.0, help="Apply cumulative volatility warp with given sigma.")
    p.add_argument("--vol-warp-window", nargs=2, metavar=("START", "END"), help="Only warp prices within this UTC window (inclusive).")
    p.add_argument("--feature-drop-rate", type=float, default=0.0, help="Probability of zeroing each feature value (0-1).")
    p.add_argument("--drop-sample", type=float, default=0.0, help="Randomly drop this fraction of samples after preprocessing.")
    return p.parse_args()


def _pip_value(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def compute_cost_return(symbol: str, price: pd.Series, spread_pips: float, slip_pips: float, comm_per_million: float) -> pd.Series:
    pip = _pip_value(symbol)
    # Approx trade cost (fractional): spread + 2*slippage in price terms + commission fraction
    frac_px = (spread_pips + 2.0 * slip_pips) * pip / price
    frac_comm = (comm_per_million / 1_000_000.0)
    return frac_px + frac_comm


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(period).mean()
    roll_down = down.rolling(period).mean()
    rs = roll_up / roll_down
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["time"], utc=True)
    hour = ts.dt.hour.astype(float)
    dow = ts.dt.dayofweek.astype(float)
    df["hour_sin"], df["hour_cos"] = np.sin(2 * np.pi * hour / 24.0), np.cos(2 * np.pi * hour / 24.0)
    df["dow_sin"], df["dow_cos"] = np.sin(2 * np.pi * dow / 7.0), np.cos(2 * np.pi * dow / 7.0)
    return df


def build_features(
    df: pd.DataFrame,
    fast: int = 20,
    slow: int = 80,
    rsi_p: int = 14,
    atr_p: int = 14,
    add_ret12: bool = False,
    add_vol48: bool = False,
    add_sma14_56: bool = False,
) -> Tuple[pd.DataFrame, List[str]]:
    out = df.copy()
    out = add_time_features(out)
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_3"] = out["close"].pct_change(3)
    out["ret_6"] = out["close"].pct_change(6)
    out["vol_24"] = out["close"].pct_change(1).rolling(24).std()
    out["ret_12"] = out["close"].pct_change(12) if add_ret12 else None
    out["vol_48"] = out["close"].pct_change(1).rolling(48).std() if add_vol48 else None
    sma_f = out["close"].rolling(fast).mean()
    sma_s = out["close"].rolling(slow).mean()
    out["sma_diff"] = (sma_f - sma_s) / out["close"]
    if add_sma14_56:
        sma_f2 = out["close"].rolling(14).mean()
        sma_s2 = out["close"].rolling(56).mean()
        out["sma_diff_14_56"] = (sma_f2 - sma_s2) / out["close"]
    out["rsi"] = rsi(out["close"], rsi_p)
    out["atr_norm"] = atr(out["high"], out["low"], out["close"], atr_p) / out["close"]
    feats = [
        "ret_1", "ret_3", "ret_6", "vol_24",
        "sma_diff", "rsi", "atr_norm",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    if add_ret12:
        feats.append("ret_12")
    if add_vol48:
        feats.append("vol_48")
    if add_sma14_56:
        feats.append("sma_diff_14_56")
    return out, feats


def forward_return(close: pd.Series, horizon: int) -> pd.Series:
    return close.shift(-horizon) / close - 1.0


def pick_thresholds(prob: np.ndarray, net_returns: np.ndarray) -> Dict[str, float]:
    best = {"thr_long": 0.6, "thr_exit": 0.5, "sharpe": -np.inf, "trades": 0}
    for thr in np.linspace(0.55, 0.8, 11):
        for thr_exit in np.linspace(0.45, 0.6, 16):
            chosen = prob >= thr
            if not np.any(chosen):
                continue
            net = net_returns[chosen]
            if net.size < 50:
                continue
            mu = float(np.nanmean(net))
            sd = float(np.nanstd(net, ddof=1))
            sharpe = (mu / sd) * math.sqrt(252 * 24) if sd > 0 else -np.inf
            if sharpe > best["sharpe"]:
                best = {"thr_long": float(thr), "thr_exit": float(thr_exit), "sharpe": sharpe, "trades": int(net.size)}
    return best


def _block_shuffle_df(df: pd.DataFrame, block_size: int) -> pd.DataFrame:
    if block_size <= 0 or block_size >= len(df):
        return df
    blocks = [df.iloc[i : i + block_size] for i in range(0, len(df), block_size)]
    if len(blocks) <= 1:
        return df
    rng = np.random.default_rng()
    rng.shuffle(blocks)
    shuffled = pd.concat(blocks, ignore_index=True)
    return shuffled


def main() -> None:
    try:
        import xgboost as xgb  # requires xgboost==1.7.6 per requirements
    except Exception as exc:
        raise SystemExit("xgboost is required. Please install xgboost==1.7.6.") from exc

    args = parse_args()
    np.random.seed(args.seed)
    rng = np.random.default_rng(args.seed)

    symbol_lower = str(args.symbol).lower()
    default_out = Path(f"QuantResearch/artifacts/models/{symbol_lower}_h1_xgb_v2")
    default_ptr = Path(f"QuantResearch/artifacts/models/{symbol_lower}_h1_xgb_latest.json")
    out_dir_root = Path(args.out) if args.out else default_out
    latest_ptr_path = Path(args.latest_ptr) if args.latest_ptr else default_ptr

    df = pd.read_csv(args.csv)
    if args.train_start or args.train_end:
        ts = pd.to_datetime(df["time"], utc=True)
        if args.train_start:
            ts_start = pd.to_datetime(args.train_start, utc=True)
            df = df[ts >= ts_start]
            ts = ts[ts >= ts_start]
        if args.train_end:
            ts_end = pd.to_datetime(args.train_end, utc=True)
            df = df[ts <= ts_end]
            ts = ts[ts <= ts_end]
        df = df.reset_index(drop=True)
    if args.vol_warp and args.vol_warp > 0:
        ts_full = pd.to_datetime(df["time"], utc=True)
        if args.vol_warp_window:
            start_str, end_str = args.vol_warp_window
            ts_start = pd.to_datetime(start_str, utc=True)
            ts_end = pd.to_datetime(end_str, utc=True)
            mask = (ts_full >= ts_start) & (ts_full <= ts_end)
            if mask.any():
                noise = np.random.normal(0.0, args.vol_warp, size=mask.sum())
                warp_seg = np.exp(noise).cumprod()
                warp = np.ones(len(df))
                warp[mask.to_numpy()] = warp_seg
                for col in ("open", "high", "low", "close"):
                    if col in df.columns:
                        df[col] = df[col].astype(float) * warp
            else:
                warnings.warn("vol-warp-window provided but no rows matched; skipping warp.")
        else:
            noise = np.random.normal(0.0, args.vol_warp, size=len(df))
            warp = np.exp(noise).cumprod()
            for col in ("open", "high", "low", "close"):
                if col in df.columns:
                    df[col] = df[col].astype(float) * warp
    if "time" not in df.columns:
        raise SystemExit("CSV must include 'time' column")
    df = df[["time", "open", "high", "low", "close", "volume"]].copy()

    df_feat, feat_list = build_features(
        df,
        add_ret12=args.add_ret12,
        add_vol48=args.add_vol48,
        add_sma14_56=args.add_sma14_56,
    )
    fwd = forward_return(df_feat["close"], args.horizon)
    costs = compute_cost_return(args.symbol, df_feat["close"], args.spread_pips, args.slip_pips, args.comm_per_million)
    # Label with cost margin
    if args.label_mode == "binary":
        y = pd.Series(np.where(fwd > costs, 1, np.where(fwd < -costs, 0, np.nan)), index=df_feat.index)
    else:
        # 2=long, 1=flat, 0=short
        y = pd.Series(np.where(fwd > costs, 2, np.where(fwd < -costs, 0, 1)), index=df_feat.index)

    if args.vol_warp_window and ((args.feature_drop_rate and args.feature_drop_rate > 0) or (args.drop_sample and args.drop_sample > 0)):
        warnings.warn("vol-warp-window used together with feature/drop sampling; results may be hard to attribute.")

    if args.drop_regime > 0:
        regime_cols = [c for c in df_feat.columns if "regime" in c.lower()]
        if regime_cols:
            rate = min(max(args.drop_regime, 0.0), 1.0)
            if args.drop_regime_mode == "row":
                keep_mask = np.random.rand(len(df_feat)) >= rate
                df_feat = df_feat.loc[keep_mask].reset_index(drop=True)
                fwd = fwd.loc[keep_mask].reset_index(drop=True)
                costs = costs.loc[keep_mask].reset_index(drop=True)
                y = y.loc[keep_mask].reset_index(drop=True)
            else:
                # feature mode: remove regime columns entirely
                df_feat = df_feat.drop(columns=regime_cols, errors="ignore")
                feat_list = [f for f in feat_list if f not in regime_cols]
        else:
            warnings.warn("--drop-regime set but no regime columns found; skipping.")
    feature_mask = [f.strip() for f in args.feature_mask.split(",") if f.strip()]
    if feature_mask:
        feat_list = [f for f in feat_list if f not in feature_mask]
        df_feat = df_feat.drop(columns=[c for c in feature_mask if c in df_feat.columns], errors="ignore")
    data = df_feat.assign(y=y, cost=costs, fwd=fwd).dropna(subset=feat_list + ["y", "fwd"]).reset_index(drop=True)
    if args.drop_sample and args.drop_sample > 0:
        rate = min(max(args.drop_sample, 0.0), 1.0)
        if rate >= 1.0:
            raise SystemExit("--drop-sample must be < 1.0")
        keep_mask = rng.random(len(data)) >= rate
        data = data.loc[keep_mask].reset_index(drop=True)
    if args.block_shuffle:
        data = _block_shuffle_df(data, int(args.block_shuffle))
    if args.feature_drop_rate and args.feature_drop_rate > 0:
        rate = min(max(args.feature_drop_rate, 0.0), 1.0)
        for feat in feat_list:
            if feat in data.columns:
                mask = rng.random(len(data)) < rate
                data.loc[mask, feat] = 0.0
    X = data[feat_list].to_numpy()
    y_arr = data["y"].astype(int).to_numpy()
    fwd_arr = data["fwd"].to_numpy()
    cost_arr = data["cost"].to_numpy()
    if args.label_shuffle:
        rate = min(max(args.label_shuffle, 0.0), 1.0)
        idx = np.arange(len(y_arr))
        if rate >= 0.999:
            y_arr = np.random.permutation(y_arr)
        elif rate > 0:
            n = int(len(y_arr) * rate)
            sel = np.random.choice(idx, size=n, replace=False)
            y_arr[sel] = np.random.permutation(y_arr[sel])
    if args.label_noise:
        rate = min(max(args.label_noise, 0.0), 1.0)
        if rate > 0:
            flip = np.random.rand(len(y_arr)) < rate
            if args.label_mode == "binary":
                y_arr[flip] = 1 - y_arr[flip]
            else:
                # For multi-class (0,1,2) rotate labels
                y_arr[flip] = (y_arr[flip] + 1) % 3

    n = len(data)
    i_train = int(n * args.train_ratio)
    i_val = int(n * (args.train_ratio + args.val_ratio))
    if i_val >= n:
        i_val = n - max(1, n // 10)

    X_train, y_train = X[:i_train], y_arr[:i_train]
    X_val, y_val = X[i_train:i_val], y_arr[i_train:i_val]
    X_test, y_test = X[i_val:], y_arr[i_val:]
    fwd_val, cost_val = fwd_arr[i_train:i_val], cost_arr[i_train:i_val]
    fwd_test, cost_test = fwd_arr[i_val:], cost_arr[i_val:]

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feat_list)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feat_list)
    dtest = xgb.DMatrix(X_test, label=y_test, feature_names=feat_list)

    if args.label_mode == "binary":
        objective = "binary:logistic"
        eval_metric = "logloss"
    else:
        objective = "multi:softprob"
        eval_metric = "mlogloss"

    params = {
        "objective": objective,
        "eval_metric": eval_metric,
        "max_depth": args.max_depth,
        "eta": args.learning_rate,
        "lambda": args.reg_lambda,
        "min_child_weight": args.min_child_weight,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "seed": args.seed,
    }
    if args.label_mode == "multi":
        params["num_class"] = 3
    elif args.scale_pos_weight and args.scale_pos_weight != 1.0:
        params["scale_pos_weight"] = args.scale_pos_weight
    evals = [(dtrain, "train"), (dval, "val")]
    booster = xgb.train(params, dtrain, num_boost_round=args.n_estimators, evals=evals, early_stopping_rounds=50, verbose_eval=False)

    thresholds_payload: Dict[str, float | Dict] = {"label_mode": args.label_mode}
    test_metrics: Dict[str, Dict[str, float]] = {}
    val_metrics: Dict[str, Dict[str, float]] = {}

    if args.label_mode == "binary":
        p_val = booster.predict(dval)
        long_thr = pick_thresholds(p_val, fwd_val - cost_val)
        p_test = booster.predict(dtest)
        chosen = p_test >= long_thr["thr_long"]
        net = (fwd_test - cost_test)[chosen]
        mu = float(np.nanmean(net)) if net.size else 0.0
        sd = float(np.nanstd(net, ddof=1)) if net.size > 1 else 0.0
        sharpe = (mu / sd) * math.sqrt(252 * 24) if sd > 0 else 0.0
        test_metrics["long"] = {"sharpe": sharpe, "trades": int(net.size)}
        val_metrics["long"] = {"sharpe": long_thr["sharpe"], "trades": int(long_thr["trades"])}
        thresholds_payload.update({"p_long": long_thr["thr_long"], "p_exit": long_thr["thr_exit"]})
    else:
        preds_val = booster.predict(dval).reshape(-1, 3)
        preds_test = booster.predict(dtest).reshape(-1, 3)
        long_thr = pick_thresholds(preds_val[:, 2], fwd_val - cost_val)
        short_thr = pick_thresholds(preds_val[:, 0], -(fwd_val + cost_val))
        thresholds_payload.update({
            "p_long": long_thr["thr_long"],
            "p_exit": long_thr["thr_exit"],
            "p_short": short_thr["thr_long"],
            "p_short_exit": short_thr["thr_exit"],
        })
        # long metrics
        chosen_long = preds_test[:, 2] >= long_thr["thr_long"]
        net_long = (fwd_test - cost_test)[chosen_long]
        mu_long = float(np.nanmean(net_long)) if net_long.size else 0.0
        sd_long = float(np.nanstd(net_long, ddof=1)) if net_long.size > 1 else 0.0
        sharpe_long = (mu_long / sd_long) * math.sqrt(252 * 24) if sd_long > 0 else 0.0
        test_metrics["long"] = {"sharpe": sharpe_long, "trades": int(net_long.size)}
        val_metrics["long"] = {"sharpe": long_thr["sharpe"], "trades": int(long_thr["trades"])}
        # short metrics
        chosen_short = preds_test[:, 0] >= short_thr["thr_long"]
        net_short = (-(fwd_test + cost_test))[chosen_short]
        mu_short = float(np.nanmean(net_short)) if net_short.size else 0.0
        sd_short = float(np.nanstd(net_short, ddof=1)) if net_short.size > 1 else 0.0
        sharpe_short = (mu_short / sd_short) * math.sqrt(252 * 24) if sd_short > 0 else 0.0
        test_metrics["short"] = {"sharpe": sharpe_short, "trades": int(net_short.size)}
        val_metrics["short"] = {"sharpe": short_thr["sharpe"], "trades": int(short_thr["trades"])}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    model_dir = out_dir_root.with_suffix("") / ts
    model_dir.mkdir(parents=True, exist_ok=True)
    # Save artifacts
    booster.save_model(str(model_dir / "model.json"))
    (model_dir / "feature_list.json").write_text(json.dumps(feat_list, indent=2), encoding="utf-8")
    thresholds_payload["val_metrics"] = val_metrics
    thresholds_payload["test_metrics"] = test_metrics
    (model_dir / "thresholds.json").write_text(json.dumps(thresholds_payload, indent=2), encoding="utf-8")
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": args.symbol,
        "csv": args.csv,
        "horizon": args.horizon,
        "splits": {"train": int(i_train), "val": int(i_val - i_train), "test": int(n - i_val)},
        "seed": int(args.seed),
        "features": feat_list,
        "xgb_params": params,
        "best_iteration": int(getattr(booster, "best_iteration", 0) or 0),
        "thresholds": thresholds_payload,
        "label_mode": args.label_mode,
        "class_mapping": {"short": 0, "flat": 1, "long": 2} if args.label_mode == "multi" else {"flat": 0, "long": 1},
        "costs": {"spread_pips": args.spread_pips, "slip_pips": args.slip_pips, "comm_per_million": args.comm_per_million},
        "stress_params": {
            "label_shuffle": args.label_shuffle,
            "label_noise": args.label_noise,
            "block_shuffle": args.block_shuffle,
            "drop_regime": args.drop_regime,
            "drop_regime_mode": args.drop_regime_mode,
            "feature_mask": feature_mask,
            "feature_drop_rate": args.feature_drop_rate,
            "drop_sample": args.drop_sample,
            "vol_warp": args.vol_warp,
            "vol_warp_window": args.vol_warp_window,
            "train_start": args.train_start,
            "train_end": args.train_end,
        },
        "git_commit": None,
    }
    (model_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    latest_path = latest_ptr_path
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps({"model_dir": str(model_dir), "label_mode": args.label_mode}, indent=2), encoding="utf-8")
    print(f"Saved model artifacts to {model_dir}")


if __name__ == "__main__":
    main()
