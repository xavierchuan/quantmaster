"""Lightweight OANDA v20 helpers shared by research and trading layers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from shared.utils import config


@dataclass
class OandaClient:
    account_id: str
    api_key: str
    api_base: str = "https://api-fxtrade.oanda.com/v3"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> Dict[str, Any]:
        url = f"{self.api_base}{path}"
        resp = requests.get(url, headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_account_summary(self) -> Dict[str, Any]:
        return self._get(f"/accounts/{self.account_id}/summary").get("account", {})


def build_default_client() -> OandaClient:
    cfg = config.load_env()
    return OandaClient(
        account_id=config.require(cfg, "OANDA_ACCOUNT_ID"),
        api_key=config.require(cfg, "OANDA_TOKEN"),
        api_base=cfg.get("OANDA_URL", "https://api-fxtrade.oanda.com/v3"),
    )


def snapshot_account(client: Optional[OandaClient] = None) -> Dict[str, Any]:
    client = client or build_default_client()
    summary = client.get_account_summary()
    now = time.time()
    nav = float(summary.get("NAV")) if summary.get("NAV") is not None else float(summary.get("balance", 0.0))
    margin_used = float(summary.get("marginUsed", 0.0))
    margin_rate = summary.get("marginRate")
    try:
        margin_rate = float(margin_rate) if margin_rate is not None else None
    except (TypeError, ValueError):
        margin_rate = None

    gross_notional = None
    leverage = None
    if margin_rate and margin_rate > 0:
        gross_notional = margin_used / margin_rate
        if nav > 0:
            leverage = gross_notional / nav

    return {
        "timestamp": now,
        "balance": float(summary.get("balance", 0.0)),
        "nav": nav,
        "unrealizedPL": float(summary.get("unrealizedPL", 0.0)),
        "marginAvailable": float(summary.get("marginAvailable", 0.0)),
        "marginUsed": margin_used,
        "currency": summary.get("currency"),
        "marginRate": margin_rate,
        "grossPositionValue": gross_notional,
        "effectiveLeverage": leverage,
    }
