"""
Signal engine — generates BUY/SELL/HOLD signals for NSE F&O.

Default strategy: Short Straddle when IV Rank > 70 and RSI is in 40–60 range
(high IV environment, neutral price action — premium selling setup).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .indicators import rsi, iv_rank

logger = logging.getLogger(__name__)


class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"    # short/sell-to-open
    HOLD = "HOLD"
    EXIT = "EXIT"    # close existing position


@dataclass
class TradeSignal:
    signal: Signal
    underlying: str
    strike: float
    option_type: str          # "CE" or "PE"
    expiry: str
    security_id: str
    reason: str
    confidence: float = 1.0   # 0–1


class ShortStraddleStrategy:
    """
    Sell ATM CE + ATM PE when:
      - IV Rank > iv_rank_threshold (high premium)
      - RSI between rsi_low and rsi_high (neutral trend)
    Exit when:
      - Combined premium decays by profit_target_pct
      - Or loss exceeds stop_loss_pct of premium collected
    """

    def __init__(
        self,
        iv_rank_threshold: float = 70.0,
        rsi_low: float = 40.0,
        rsi_high: float = 60.0,
        profit_target_pct: float = 0.50,   # exit at 50% profit
        stop_loss_pct: float = 1.00,       # stop at 2x premium (100% loss)
    ):
        self.iv_rank_threshold = iv_rank_threshold
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct

    def generate_entry_signals(
        self,
        underlying: str,
        expiry: str,
        option_chain: dict[str, Any],
        close_prices: list[float],
        iv_history: list[float],
    ) -> list[TradeSignal]:
        """Return entry signals (SELL CE + SELL PE) if conditions are met."""
        if not option_chain or not close_prices:
            return []

        current_rsi = rsi(close_prices)
        spot = close_prices[-1]

        # Find ATM IV from option chain
        atm_data = self._get_atm_strike(spot, option_chain)
        if not atm_data:
            return []

        current_iv = atm_data.get("ce_iv", 0) or atm_data.get("pe_iv", 0)
        iv_r = iv_rank(current_iv, iv_history)

        logger.info(
            "%s | Spot=%.2f ATM=%.0f IV_Rank=%.1f RSI=%.1f",
            underlying, spot, atm_data["strike"], iv_r, current_rsi,
        )

        if iv_r < self.iv_rank_threshold:
            logger.info("IV Rank %.1f below threshold — no entry", iv_r)
            return []

        if not (self.rsi_low <= current_rsi <= self.rsi_high):
            logger.info("RSI %.1f outside neutral range — no entry", current_rsi)
            return []

        reason = f"IV_Rank={iv_r:.1f} RSI={current_rsi:.1f}"

        return [
            TradeSignal(
                signal=Signal.SELL,
                underlying=underlying,
                strike=atm_data["strike"],
                option_type="CE",
                expiry=expiry,
                security_id=atm_data["ce_security_id"],
                reason=reason,
                confidence=min(iv_r / 100, 1.0),
            ),
            TradeSignal(
                signal=Signal.SELL,
                underlying=underlying,
                strike=atm_data["strike"],
                option_type="PE",
                expiry=expiry,
                security_id=atm_data["pe_security_id"],
                reason=reason,
                confidence=min(iv_r / 100, 1.0),
            ),
        ]

    def check_exit(
        self,
        position: dict[str, Any],
        current_premium: float,
    ) -> TradeSignal | None:
        """Return EXIT signal if profit target or stop-loss is hit."""
        entry_premium = position["entry_premium"]
        pnl_pct = (entry_premium - current_premium) / entry_premium

        if pnl_pct >= self.profit_target_pct:
            return TradeSignal(
                signal=Signal.EXIT,
                underlying=position["underlying"],
                strike=position["strike"],
                option_type=position["option_type"],
                expiry=position["expiry"],
                security_id=position["security_id"],
                reason=f"Profit target hit ({pnl_pct:.1%})",
            )

        if pnl_pct <= -self.stop_loss_pct:
            return TradeSignal(
                signal=Signal.EXIT,
                underlying=position["underlying"],
                strike=position["strike"],
                option_type=position["option_type"],
                expiry=position["expiry"],
                security_id=position["security_id"],
                reason=f"Stop loss hit ({pnl_pct:.1%})",
            )

        return None

    def _get_atm_strike(self, spot: float, option_chain: dict) -> dict | None:
        """Find the ATM strike closest to spot price."""
        strikes = option_chain.get("strike_list", [])
        if not strikes:
            return None

        atm_strike = min(strikes, key=lambda s: abs(s - spot))
        chain = option_chain.get("options_data", {}).get(str(int(atm_strike)), {})

        if not chain:
            return None

        return {
            "strike": atm_strike,
            "ce_security_id": chain.get("call_options", {}).get("security_id", ""),
            "pe_security_id": chain.get("put_options", {}).get("security_id", ""),
            "ce_iv": chain.get("call_options", {}).get("implied_volatility", 0),
            "pe_iv": chain.get("put_options", {}).get("implied_volatility", 0),
        }
