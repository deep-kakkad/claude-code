"""
Market data fetcher — option chain snapshots and historical OHLC.
"""
from __future__ import annotations

import logging
from typing import Any

from dhanhq import DhanContext, dhanhq

from config import DhanConfig

logger = logging.getLogger(__name__)


class MarketData:
    def __init__(self, cfg: DhanConfig):
        ctx = DhanContext(cfg.client_id, cfg.access_token)
        self._dhan = dhanhq(ctx)

    def get_option_chain(self, underlying: str, expiry_date: str) -> dict[str, Any]:
        """
        Fetch option chain with Greeks for a given underlying and expiry.
        underlying: e.g. "NIFTY"
        expiry_date: e.g. "2025-06-26"
        """
        resp = self._dhan.get_option_chain(
            underlying=underlying,
            expiry_date=expiry_date,
        )
        if resp.get("status") != "success":
            logger.error("Option chain fetch failed: %s", resp)
            return {}
        return resp.get("data", {})

    def get_expiry_list(self, underlying: str) -> list[str]:
        """Return sorted list of available expiry dates for the underlying."""
        resp = self._dhan.get_expiry_list(underlying=underlying)
        if resp.get("status") != "success":
            return []
        return resp.get("data", [])

    def get_historical_ohlc(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        from_date: str,
        to_date: str,
        interval: str = "1",  # minutes; "D" for daily
    ) -> list[dict]:
        resp = self._dhan.historical_daily_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=to_date,
        )
        if resp.get("status") != "success":
            logger.error("OHLC fetch failed: %s", resp)
            return []
        return resp.get("data", [])

    def get_intraday_ohlc(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        interval: int = 5,
        from_date: str = "",
        to_date: str = "",
    ) -> list[dict]:
        """Fetch intraday OHLC candles (default 5-min) for today."""
        from datetime import date
        today = date.today().isoformat()
        resp = self._dhan.intraday_minute_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            interval=interval,
            from_date=from_date or today,
            to_date=to_date or today,
        )
        if resp.get("status") != "success":
            logger.error("Intraday OHLC fetch failed: %s", resp)
            return []
        return resp.get("data", [])

    def get_ltp(self, securities: list[dict]) -> dict[str, float]:
        """
        Get Last Traded Price for a list of securities.
        securities: [{"exchange_segment": "NSE_FNO", "security_id": "...", "instrument_type": "OPTIDX"}]
        """
        resp = self._dhan.get_market_quote(securities=securities)
        if resp.get("status") != "success":
            return {}
        prices = {}
        for item in resp.get("data", []):
            prices[item["security_id"]] = item.get("last_trade_price", 0.0)
        return prices
