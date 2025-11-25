# data/csv_feed.py
import time
from threading import Thread

import numpy as np
import pandas as pd
from loguru import logger

_TIME_CANDIDATES = ["ts", "time", "datetime", "date"]

class CSVFeed:
    def __init__(self, q, path: str, symbol: str, speed: float = 0.0, outlier_threshold: float = 5.0):
        """
        q: queue.Queue，用于向主循环推送事件
        path: CSV 路径
        symbol: 交易对符号（例如 EURUSD）
        speed: 流式模式下每条bar之间的sleep秒数；为0表示尽快
        """
        self.q = q
        self.path = path
        self.symbol = symbol
        self.speed = float(speed)
        self.finished = False
        self._thread = None
        self.idx = 0
        self.outlier_threshold = float(outlier_threshold)

        # 读CSV
        df = pd.read_csv(self.path)

        # 自动识别时间列 -> 统一命名为 ts 并转换为UTC时间戳
        time_col = None
        for c in _TIME_CANDIDATES:
            if c in df.columns:
                time_col = c
                break
        if time_col is None:
            raise ValueError(f"No timestamp column found; expected one of: {_TIME_CANDIDATES}")
        if time_col != "ts":
            df = df.rename(columns={time_col: "ts"})
        # 兼容 OANDA ISO8601（带Z）；统一到UTC
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        if df["ts"].isna().any():
            bad = df[df["ts"].isna()]
            raise ValueError(f"Found unparsable timestamps in column '{time_col}'. Examples:\n{bad.head()}")

        # 基础列校验：open/high/low/close/volume
        need_cols = ["open", "high", "low", "close"]
        for c in need_cols:
            if c not in df.columns:
                raise ValueError(f"CSV missing required column: {c}")

        # volume 可选
        if "volume" not in df.columns:
            df["volume"] = 0

        # 排序、重建索引
        df = df.sort_values("ts").reset_index(drop=True)
        df["outlier"] = self._flag_outliers(df)

        self.df = df
        self.n = len(df)

        logger.info(f"Loaded {self.n} rows from {self.path} for {self.symbol}")

    def _flag_outliers(self, df: pd.DataFrame) -> pd.Series:
        """
        对数值列（close/volume）进行 z-score > threshold 标记，标记为 True 的 bar 允许被策略忽略。
        """
        mask = pd.Series(False, index=df.index)
        numeric_cols = [col for col in ("close", "volume") if col in df.columns]
        if not numeric_cols:
            return mask
        for col in numeric_cols:
            series = pd.to_numeric(df[col], errors="coerce")
            std = series.std(ddof=0)
            mean = series.mean()
            if std is None or std == 0 or np.isnan(std):
                continue
            zscores = np.abs((series - mean) / std)
            mask = mask | (zscores > self.outlier_threshold)
        return mask.fillna(False)

    def _emit_row(self, i: int):
        row = self.df.iloc[i]
        event = {
            "type": "bar",
            "symbol": self.symbol,
            "ts": row["ts"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row.get("volume", 0)),
            "outlier": bool(row.get("outlier", False)),
            # 你也可以在这里加上更多字段，比如 "i": i
        }
        # Optional regime labels if present in CSV
        if "vol_regime" in row:
            event["vol_regime"] = row["vol_regime"]
        elif "vol_high" in row or "vol_low" in row:
            if row.get("vol_high"):
                event["vol_regime"] = "high"
            elif row.get("vol_low"):
                event["vol_regime"] = "low"
        if "trend_regime" in row:
            event["trend_regime"] = row["trend_regime"]
        self.q.put(event)

    def pump(self, n: int = 1):
        """
        批量推送 n 条到队列（同步方式，适合回测快跑）
        如果到末尾，会发送 'eof' 并将 finished=True
        """
        if self.finished:
            return

        emitted = 0
        while emitted < n and self.idx < self.n:
            self._emit_row(self.idx)
            self.idx += 1
            emitted += 1

        if self.idx >= self.n and not self.finished:
            # EOF
            self.q.put({"type": "eof", "symbol": self.symbol})
            self.finished = True

    def start(self):
        """
        启动后台线程，流式逐条推送（用于模拟实时）
        """
        if self._thread is not None:
            return
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self.finished and self.idx < self.n:
            self._emit_row(self.idx)
            self.idx += 1
            if self.idx >= self.n:
                self.q.put({"type": "eof", "symbol": self.symbol})
                self.finished = True
                break
            if self.speed > 0:
                time.sleep(self.speed)
