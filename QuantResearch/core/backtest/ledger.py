"""
Lightweight append-only trading ledger with replay support.

This module is intentionally minimal: it records events with monotonically
increasing sequence ids and versions, persists them as JSONL, and can replay the
log to recover a local state (cash, positions, open orders).

The intent is to provide a deterministic, truncatable log that can be replayed
before connecting to a remote broker, so the engine can reconcile local state
with remote accounts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PositionState:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0


@dataclass
class OrderState:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


@dataclass
class LocalState:
    cash: float = 0.0
    positions: Dict[str, PositionState] = field(default_factory=dict)
    open_orders: Dict[str, OrderState] = field(default_factory=dict)


class Ledger:
    """
    Append-only ledger backed by JSONL. Each event gets a sequence_id and
    version (start at 1). Replay produces a LocalState snapshot.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._seq = 0
        self._version = 1
        self.events: List[dict] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.events.append(ev)
                self._seq = max(self._seq, int(ev.get("sequence_id", 0)))
                self._version = max(self._version, int(ev.get("version", 1)))

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def append(self, event_type: str, data: dict) -> dict:
        ev = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "sequence_id": self._next_seq(),
            "version": self._version,
            "type": event_type,
            "data": data,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self.events.append(ev)
        return ev

    def replay(self, up_to_sequence: Optional[int] = None) -> LocalState:
        state = LocalState()
        for ev in self.events:
            if up_to_sequence is not None and ev.get("sequence_id", 0) > up_to_sequence:
                break
            self._apply_event(state, ev)
        return state

    def _apply_event(self, state: LocalState, ev: dict) -> None:
        etype = ev.get("type")
        data = ev.get("data", {})

        if etype == "cash_adjust":
            amt = float(data.get("amount", 0.0))
            state.cash += amt
            return

        if etype == "order_submitted":
            order = OrderState(
                order_id=str(data.get("order_id")),
                symbol=str(data.get("symbol")),
                side=str(data.get("side")).upper(),
                quantity=float(data.get("quantity", 0.0)),
                price=data.get("price"),
                sl=data.get("sl"),
                tp=data.get("tp"),
            )
            state.open_orders[order.order_id] = order
            return

        if etype == "order_cancelled":
            oid = str(data.get("order_id"))
            state.open_orders.pop(oid, None)
            return

        if etype == "fill":
            oid = str(data.get("order_id", ""))
            side = str(data.get("side", "")).upper()
            qty = float(data.get("quantity", 0.0))
            price = float(data.get("price", 0.0))
            state.open_orders.pop(oid, None)
            symbol = str(data.get("symbol"))
            pos = state.positions.get(symbol, PositionState(symbol=symbol))
            direction = 1.0 if side in ("BUY", "LONG") else -1.0
            new_qty = pos.quantity + direction * qty
            if new_qty == 0:
                pos.quantity = 0.0
                pos.avg_price = 0.0
            else:
                if pos.quantity == 0:
                    pos.avg_price = price
                elif (pos.quantity > 0) == (direction > 0):
                    pos.avg_price = (pos.avg_price * pos.quantity + price * qty * direction) / new_qty
                else:
                    # Closing or reducing: keep avg_price of remaining exposure
                    pass
                pos.quantity = new_qty
            state.positions[symbol] = pos

            notional = qty * price
            if direction > 0:
                state.cash -= notional
            else:
                state.cash += notional
            commission = float(data.get("commission", 0.0))
            state.cash -= abs(commission)
            return

        if etype == "position_sync":
            symbol = str(data.get("symbol"))
            qty = float(data.get("quantity", 0.0))
            avg = float(data.get("avg_price", 0.0))
            state.positions[symbol] = PositionState(symbol=symbol, quantity=qty, avg_price=avg)
            return

        if etype == "set_cash":
            state.cash = float(data.get("cash", state.cash))
            return

        # Unknown events are ignored to keep replay forward-compatible.

