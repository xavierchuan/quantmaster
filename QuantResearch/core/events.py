# fx_backtest/core/events.py
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

# Ensure imports resolve when running scripts directly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass(frozen=True)
class TickEvent:
    ts: datetime
    symbol: str
    bid: float
    ask: float


@dataclass(frozen=True)
class SignalEvent:
    ts: datetime
    symbol: str
    direction: Literal["LONG", "SHORT", "EXIT"]
    size: float  # 合约单位


@dataclass(frozen=True)
class OrderEvent:
    ts: datetime
    symbol: str
    side: Literal["BUY", "SELL"]
    size: float
    price: Optional[float] = None  # 市价时为空；限价时填价格


@dataclass(frozen=True)
class FillEvent:
    ts: datetime
    symbol: str
    side: Literal["BUY", "SELL"]
    size: float
    price: float
    commission: float
