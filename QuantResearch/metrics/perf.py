"""
绩效与交易统计相关的通用函数。

这些函数原本实现在 scripts/backtest_strategy.py 中，
迁移到独立模块便于其它组件直接复用。
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple, Union

import numpy as np
import pandas as pd


EquityPoint = Tuple[Union[str, "pd.Timestamp"], float]
TradeRecord = dict


def compute_metrics(equity_series: Sequence[EquityPoint], bars_per_year: int = 24 * 252) -> dict:
    """
    根据权益曲线计算常见绩效指标（年化收益、波动率、夏普、Sortino、Calmar、回撤长度等）。
    `equity_series` 需为 (timestamp, equity) 序列。
    """
    if not equity_series:
        return {}

    equity_series = sorted(equity_series, key=lambda x: x[0])
    eq = np.asarray([equity for _, equity in equity_series], dtype=float)
    if eq.size < 2:
        return {}

    rets = np.diff(eq) / eq[:-1]
    if rets.size == 0:
        return {}

    mean = rets.mean()
    std = rets.std(ddof=1) if rets.size > 1 else 0.0
    ann_ret = (1 + mean) ** bars_per_year - 1 if mean > -1 else -1
    ann_vol = std * np.sqrt(bars_per_year) if std > 0 else 0.0
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0

    peak = np.maximum.accumulate(eq)
    drawdown = (eq - peak) / peak
    max_dd = drawdown.min() if drawdown.size else 0.0

    downside = rets[rets < 0]
    downside_std = np.sqrt(np.mean(downside ** 2)) if downside.size else 0.0
    ann_downside_std = downside_std * np.sqrt(bars_per_year) if downside_std > 0 else 0.0
    sortino = ann_ret / ann_downside_std if ann_downside_std > 0 else 0.0
    calmar = ann_ret / abs(max_dd) if max_dd < 0 else 0.0

    durations = []
    duration = 0
    for value, pk in zip(eq, peak):
        if pk > 0 and value + 1e-12 < pk:
            duration += 1
        else:
            if duration > 0:
                durations.append(duration)
                duration = 0
    current_duration = duration
    all_durations = durations + ([current_duration] if current_duration else [])
    max_duration = max(all_durations) if all_durations else 0
    avg_duration = float(np.mean(durations)) if durations else 0.0

    return {
        "final_equity": float(eq[-1]),
        "ann_return": float(ann_ret),
        "ann_vol": float(ann_vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "max_drawdown_duration_bars": int(max_duration),
        "avg_drawdown_duration_bars": float(avg_duration),
        "current_drawdown_duration_bars": int(current_duration),
        "recovery_time_bars": int(max_duration),
    }


def _pair_trades_for_duration(trade_log: Iterable[TradeRecord]) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """将 trade_log 中的进场/出场配对用于停留时间统计。"""
    pairs: List[Tuple[pd.Timestamp, pd.Timestamp]] = []
    current_entry = None
    for record in trade_log:
        ts_entry = record.get("ts_entry")
        if ts_entry is not None:
            current_entry = ts_entry
        ts_exit = record.get("ts_exit")
        if ts_exit is not None and current_entry is not None:
            pairs.append((current_entry, ts_exit))
            current_entry = None
    return pairs


def trade_stats(trade_log: Sequence[TradeRecord]) -> dict:
    """
    基于 trade_log 计算胜率、平均盈亏、盈亏比、单笔期望与中位持仓时间。
    传入的 trade_log 应包含 `pnl`、`ts_entry`、`ts_exit` 等字段。
    """
    if not trade_log:
        return {}

    df = pd.DataFrame(trade_log)
    if "pnl" not in df.columns:
        return {}

    closed = df.dropna(subset=["pnl"])
    if closed.empty:
        return {}

    win_mask = closed["pnl"] > 0
    loss_mask = closed["pnl"] <= 0
    avg_win = closed.loc[win_mask, "pnl"].mean() if win_mask.any() else np.nan
    avg_loss_raw = closed.loc[loss_mask, "pnl"].mean() if loss_mask.any() else np.nan
    avg_loss = -avg_loss_raw if pd.notna(avg_loss_raw) else np.nan

    if pd.notna(avg_win) and pd.notna(avg_loss) and avg_loss > 0:
        rr = avg_win / avg_loss
        rr = rr if np.isfinite(rr) and rr > 0 else np.nan
    else:
        rr = np.nan

    expectancy = closed["pnl"].mean()
    win_rate = (closed["pnl"] > 0).mean()

    median_hold = None
    pairs = _pair_trades_for_duration(trade_log)
    if pairs:
        t_entry = pd.to_datetime([p[0] for p in pairs], errors="coerce")
        t_exit = pd.to_datetime([p[1] for p in pairs], errors="coerce")
        duration = (t_exit - t_entry).dropna()
        if not duration.empty:
            median_hold = duration.median()

    return {
        "win_rate": float(win_rate),
        "avg_win": float(avg_win) if pd.notna(avg_win) else None,
        "avg_loss": float(avg_loss) if pd.notna(avg_loss) else None,
        "rr": float(rr) if pd.notna(rr) else None,
        "expectancy": float(expectancy),
        "median_hold": str(median_hold) if median_hold is not None else None,
    }


__all__ = ["compute_metrics", "trade_stats"]
