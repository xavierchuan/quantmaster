"""Kill switch utilities for leverage/margin protection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

from loguru import logger


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _margin_ratio(snapshot: Dict[str, Any]) -> Optional[float]:
    used = _safe_float(snapshot.get("marginUsed"))
    avail = _safe_float(snapshot.get("marginAvailable"))
    denom = used + avail
    if denom <= 0:
        return None
    return avail / denom


def _effective_leverage(snapshot: Dict[str, Any]) -> Optional[float]:
    val = snapshot.get("effectiveLeverage")
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


@dataclass
class KillSwitch:
    nav_floor: Optional[float] = None
    margin_ratio_floor: Optional[float] = None
    leverage_ceiling: Optional[float] = None

    def should_trigger(self, snapshot: Dict[str, Any]) -> Optional[str]:
        """Return reason string if kill switch should trigger."""

        nav = _safe_float(snapshot.get("nav"))
        if self.nav_floor is not None and nav > 0 and nav < self.nav_floor:
            return f"NAV {nav:.2f} below floor {self.nav_floor:.2f}"

        margin_ratio = _margin_ratio(snapshot)
        if (
            self.margin_ratio_floor is not None
            and margin_ratio is not None
            and margin_ratio < self.margin_ratio_floor
        ):
            return (
                f"Margin ratio {margin_ratio:.3f} below floor "
                f"{self.margin_ratio_floor:.3f}"
            )

        leverage = _effective_leverage(snapshot)
        if (
            self.leverage_ceiling is not None
            and leverage is not None
            and leverage > self.leverage_ceiling
        ):
            return (
                f"Leverage {leverage:.2f} exceeds ceiling "
                f"{self.leverage_ceiling:.2f}"
            )
        return None

    @property
    def enabled(self) -> bool:
        return any(
            threshold is not None
            for threshold in (self.nav_floor, self.margin_ratio_floor, self.leverage_ceiling)
        )

    def log_trigger(self, reason: str) -> None:
        logger.error("[KILL SWITCH] Triggered: %s", reason)
