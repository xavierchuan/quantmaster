#!/usr/bin/env python
"""
Portfolio optimizer for multi-sleeve FX strategies.

Loads sleeve equity curves, computes returns, and solves for optimal weights
under different objectives (max Sharpe, min CVaR, risk parity) with optional
constraints on drawdown, CVaR, and per-asset weight caps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from loguru import logger

try:
    from scipy.optimize import minimize
except ImportError as exc:  # pragma: no cover
    raise SystemExit("scipy is required for portfolio_optimizer.py") from exc


BARs_PER_YEAR = 24 * 252  # H1 bars per trading year


def _parse_caps(values: Optional[Sequence[str]]) -> Dict[str, float]:
    caps: Dict[str, float] = {}
    if not values:
        return caps
    for item in values:
        if "=" not in item:
            raise ValueError(f"Invalid cap specification '{item}', expected format sym=0.2")
        sym, val = item.split("=", 1)
        caps[sym.strip()] = float(val)
    return caps


def _load_equity(path: Path) -> Tuple[pd.DatetimeIndex, pd.DataFrame]:
    df = pd.read_csv(path, parse_dates=["ts"])
    cols = [c for c in df.columns if c not in ("ts", "portfolio_equity")]
    if not cols:
        raise ValueError("No sleeve columns found in equity CSV")
    sleeves = df[cols].copy()
    sleeves.index = df["ts"]
    return df["ts"], sleeves


def _compute_returns(equity_df: pd.DataFrame) -> pd.DataFrame:
    rets = equity_df.pct_change().fillna(0.0)
    return rets


class PortfolioModel:
    def __init__(
        self,
        returns_df: pd.DataFrame,
        names: Sequence[str],
        cvar_alpha: float = 0.95,
        risk_free: float = 0.0,
    ) -> None:
        self.returns_matrix = returns_df[names].to_numpy(dtype=float)
        self.names = list(names)
        self.mean = self.returns_matrix.mean(axis=0)
        self.cov = np.cov(self.returns_matrix, rowvar=False)
        self.cvar_alpha = cvar_alpha
        self.risk_free = risk_free

    def portfolio_returns(self, weights: np.ndarray) -> np.ndarray:
        return self.returns_matrix @ weights

    def stats(self, weights: np.ndarray) -> Dict[str, float]:
        port_rets = self.portfolio_returns(weights)
        mean = port_rets.mean()
        std = port_rets.std(ddof=1)
        ann_ret = (1 + mean) ** BARs_PER_YEAR - 1 if mean > -1 else -1
        ann_vol = std * np.sqrt(BARs_PER_YEAR) if std > 0 else 0.0
        sharpe = (ann_ret - self.risk_free) / ann_vol if ann_vol > 0 else 0.0

        curve = np.cumprod(1 + port_rets)
        peak = np.maximum.accumulate(curve)
        dd = np.divide(curve - peak, peak, out=np.zeros_like(curve), where=peak != 0)
        max_dd = float(dd.min()) if dd.size else 0.0

        losses = -port_rets
        if losses.size == 0:
            cvar = 0.0
        else:
            var = np.quantile(losses, self.cvar_alpha)
            tail = losses[losses >= var]
            cvar = float(tail.mean()) if tail.size else float(var)

        return {
            "ann_return": float(ann_ret),
            "ann_vol": float(ann_vol),
            "sharpe": float(sharpe),
            "max_drawdown": max_dd,
            "cvar": cvar,
        }

    def risk_contributions(self, weights: np.ndarray) -> np.ndarray:
        marginal = self.cov @ weights
        total_var = weights.T @ marginal
        if total_var <= 0:
            return np.zeros_like(weights)
        return weights * marginal / total_var


def solve_weights(
    model: PortfolioModel,
    objective: str,
    min_weight: float,
    caps: Dict[str, float],
    max_dd: Optional[float],
    cvar_limit: Optional[float],
    cvar_penalty: float,
    min_ann_return: Optional[float] = None,
) -> Tuple[np.ndarray, Dict[str, float]]:
    n = len(model.names)
    x0 = np.full(n, 1.0 / n)

    bounds = []
    for name in model.names:
        cap = caps.get(name, 1.0)
        bounds.append((min_weight, cap))

    def metrics(w: np.ndarray) -> Dict[str, float]:
        return model.stats(w)

    def obj_max_sharpe(w: np.ndarray) -> float:
        stats = metrics(w)
        score = -stats["sharpe"]
        if cvar_penalty > 0:
            score += cvar_penalty * stats["cvar"]
        return score

    def obj_min_cvar(w: np.ndarray) -> float:
        stats = metrics(w)
        return stats["cvar"]

    def obj_risk_parity(w: np.ndarray) -> float:
        cov_penalty = 0.0
        contrib = model.risk_contributions(w)
        if not contrib.any():
            cov_penalty = 1e3
        target = np.full_like(contrib, 1.0 / len(contrib))
        return float(np.sum((contrib - target) ** 2) + cov_penalty)

    objective_map = {
        "max_sharpe": obj_max_sharpe,
        "min_cvar": obj_min_cvar,
        "risk_parity": obj_risk_parity,
    }
    if objective not in objective_map:
        raise ValueError(f"Unsupported objective {objective}")

    constraints: List[Dict] = [
        {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
    ]

    if max_dd is not None:
        constraints.append({"type": "ineq", "fun": lambda w, limit=max_dd: limit + metrics(w)["max_drawdown"]})
    if cvar_limit is not None:
        constraints.append({"type": "ineq", "fun": lambda w, limit=cvar_limit: limit - metrics(w)["cvar"]})
    if min_ann_return is not None:
        constraints.append({"type": "ineq", "fun": lambda w, target=min_ann_return: metrics(w)["ann_return"] - target})

    result = minimize(
        objective_map[objective],
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-9},
    )
    if not result.success:
        raise RuntimeError(f"Optimization failed: {result.message}")
    optimal_weights = result.x
    stats = metrics(optimal_weights)
    stats["objective_value"] = float(result.fun)
    stats["status"] = result.message
    return optimal_weights, stats


def save_output(
    out_path: Path,
    names: Sequence[str],
    weights: np.ndarray,
    stats: Dict[str, float],
    constraints: Dict[str, Any],
) -> None:
    data = {
        "weights": {name: float(w) for name, w in zip(names, weights)},
        "metrics": stats,
        "constraints": constraints,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    logger.info("Saved optimizer output to %s", out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Optimize FX portfolio weights.")
    parser.add_argument("--equity-csv", required=True, help="Path to portfolio_equity.csv")
    parser.add_argument(
        "--objective",
        choices=("max_sharpe", "risk_parity", "min_cvar"),
        default="max_sharpe",
        help="Optimization target",
    )
    parser.add_argument("--cvar-alpha", type=float, default=0.95, help="CVaR confidence level")
    parser.add_argument("--cvar-limit", type=float, help="Maximum allowable CVaR (loss units)")
    parser.add_argument("--cvar-penalty", type=float, default=0.0, help="Penalty weight applied to CVaR in objective")
    parser.add_argument("--max-dd", type=float, help="Maximum allowable drawdown (e.g., 0.05 for 5%)")
    parser.add_argument("--cap", action="append", help="Per-sleeve cap, e.g., gbpjpy_h1_xgb_baseline.yaml=0.2")
    parser.add_argument("--min-weight", type=float, default=0.0, help="Global minimum weight (default long-only)")
    parser.add_argument("--risk-free", type=float, default=0.0, help="Annualized risk-free rate for Sharpe calc")
    parser.add_argument("--min-ann-return", type=float, help="Minimum annualized return constraint (e.g., 0.03)")
    parser.add_argument("--output", type=str, default="QuantResearch/results/portfolio_optimizer.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    equity_path = Path(args.equity_csv).expanduser().resolve()
    _, equity_df = _load_equity(equity_path)
    returns_df = _compute_returns(equity_df)
    names = list(equity_df.columns)

    caps = _parse_caps(args.cap)
    model = PortfolioModel(returns_df, names, cvar_alpha=args.cvar_alpha, risk_free=args.risk_free)
    weights, stats = solve_weights(
        model,
        objective=args.objective,
        min_weight=args.min_weight,
        caps=caps,
        max_dd=args.max_dd,
        cvar_limit=args.cvar_limit,
        cvar_penalty=args.cvar_penalty,
        min_ann_return=args.min_ann_return,
    )
    constraint_snapshot = {
        "objective": args.objective,
        "cvar_alpha": args.cvar_alpha,
        "cvar_limit": args.cvar_limit,
        "cvar_penalty": args.cvar_penalty,
        "max_dd": args.max_dd,
        "caps": args.cap,
        "min_weight": args.min_weight,
        "risk_free": args.risk_free,
        "min_ann_return": args.min_ann_return,
        "equity_csv": args.equity_csv,
    }
    save_output(Path(args.output).expanduser().resolve(), names, weights, stats, constraint_snapshot)


if __name__ == "__main__":
    main()
