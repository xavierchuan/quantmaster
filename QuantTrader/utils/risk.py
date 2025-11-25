"""Helpers for loading and applying execution risk profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

DEFAULT_RISK_PROFILE = Path(__file__).resolve().parents[1] / "config" / "risk_profile.yaml"


@dataclass
class RiskProfile:
    risk_scale: float = 1.0
    max_leverage: Optional[float] = None
    nav_floor: Optional[float] = None
    margin_ratio_floor: Optional[float] = None
    source: Optional[Path] = None


def load_risk_profile(path: Optional[str | Path] = None) -> RiskProfile:
    """Load risk profile from YAML, returning defaults if missing."""

    profile = RiskProfile()
    file_path = Path(path).expanduser() if path else DEFAULT_RISK_PROFILE
    profile.source = file_path
    if not file_path.exists():
        logger.warning("Risk profile %s not found; using defaults", file_path)
        return profile
    try:
        with file_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("Failed to read risk profile %s: %s", file_path, exc)
        return profile

    def _get_float(key: str, default: Optional[float]) -> Optional[float]:
        val = data.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            logger.warning("Risk profile %s has invalid value for %s: %r", file_path, key, val)
            return default

    profile.risk_scale = _get_float("risk_scale", profile.risk_scale) or profile.risk_scale
    profile.max_leverage = _get_float("max_leverage", profile.max_leverage)
    profile.nav_floor = _get_float("nav_floor", profile.nav_floor)
    profile.margin_ratio_floor = _get_float("margin_ratio_floor", profile.margin_ratio_floor)
    return profile
