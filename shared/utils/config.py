"""
Shared configuration helpers for OANDA credentials and other secrets.

Loads values from environment variables so scripts can simply import here.
"""

from __future__ import annotations

import os
from typing import Mapping


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Source .env.demo/.env.live before running.")
    return value


def load_env() -> dict[str, str]:
    """Return a copy of current environment (useful for downstream helpers)."""
    return dict(os.environ)


def require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Source .env.demo/.env.live before running.")
    return value


OANDA_ACCOUNT_ID: str = _require("OANDA_ACCOUNT_ID")
OANDA_TOKEN: str = _require("OANDA_TOKEN")
OANDA_URL: str = os.environ.get("OANDA_URL", "https://api-fxpractice.oanda.com/v3")

# Optional monitoring variables (not all scripts need them)
SLACK_RISK_WEBHOOK: str | None = os.environ.get("SLACK_RISK_WEBHOOK")
PUSHGATEWAY_URL: str | None = os.environ.get("PUSHGATEWAY_URL")
