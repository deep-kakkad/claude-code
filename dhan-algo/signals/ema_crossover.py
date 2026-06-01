"""
EMA Crossover Strategy for NIFTY F&O — both CALL and PUT.

CALL entry : 21 EMA crossed ABOVE 51 EMA → buy ITM CE (strike below spot)
PUT  entry : 21 EMA crossed BELOW 51 EMA → buy ITM PE (strike above spot)

One trade at a time. Uses 5-min candles.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .indicators import ema, crossed_above
from .strategy import Signal, TradeSignal

logger = logging.getLogger(__name__)

NIFTY_STRIKE_STEP = 50


@dataclass
class CrossoverSignal:
    fired: bool
    direction: str        # "CE" or "PE" or ""
    spot: float
    ema_fast: float
    ema_slow: float
    strike: float
    expiry: str
    security_id: str
    reason: str


class EmaCrossoverStrategy:
    """
    21 EMA × 51 EMA crossover on 5-min NIFTY candles.
    Bullish cross → buy ITM CE. Bearish cross → buy ITM PE.
    """

    def __init__(self, fast: int = 21, slow: int = 51, strike_step: int = NIFTY_STRIKE_STEP):
        self.fast = fast
        self.slow = slow
        self.strike_step = strike_step

    def evaluate(
        self,
        candles: list[dict],
        option_chain: dict[str, Any],
        expiry: str,
    ) -> CrossoverSignal:
        closes = [c["close"] for c in candles]
        spot   = closes[-1]

        ema_f = ema(closes, self.fast)
        ema_s = ema(closes, self.slow)

        bull = crossed_above(ema_f, ema_s)   # 21 crossed above 51 → BUY CE
        bear = crossed_above(ema_s, ema_f)   # 51 crossed above 21 → BUY PE

        logger.info(
            "NIFTY spot=%.2f  21EMA=%.2f  51EMA=%.2f  bull=%s  bear=%s",
            spot, ema_f[-1], ema_s[-1], bull, bear,
        )

        if not bull and not bear:
            return CrossoverSignal(
                fired=False, direction="", spot=spot,
                ema_fast=ema_f[-1], ema_slow=ema_s[-1],
                strike=0, expiry=expiry, security_id="", reason="No crossover",
            )

        if bull:
            direction = "CE"
            strike    = self._itm_call_strike(spot)   # below spot
            reason    = (
                f"21EMA({ema_f[-1]:.1f}) crossed ABOVE 51EMA({ema_s[-1]:.1f}) "
                f"— bullish | spot={spot:.1f} ITM CE strike={strike:.0f}"
            )
        else:
            direction = "PE"
            strike    = self._itm_put_strike(spot)    # above spot
            reason    = (
                f"21EMA({ema_f[-1]:.1f}) crossed BELOW 51EMA({ema_s[-1]:.1f}) "
                f"— bearish | spot={spot:.1f} ITM PE strike={strike:.0f}"
            )

        security_id = self._lookup_security_id(option_chain, strike, direction)

        return CrossoverSignal(
            fired=True, direction=direction, spot=spot,
            ema_fast=ema_f[-1], ema_slow=ema_s[-1],
            strike=strike, expiry=expiry,
            security_id=security_id, reason=reason,
        )

    def to_trade_signal(self, cs: CrossoverSignal) -> TradeSignal:
        return TradeSignal(
            signal=Signal.BUY,
            underlying="NIFTY",
            strike=cs.strike,
            option_type=cs.direction,
            expiry=cs.expiry,
            security_id=cs.security_id,
            reason=cs.reason,
        )

    def _itm_call_strike(self, spot: float) -> float:
        """Nearest strike BELOW spot = ITM for calls."""
        return (spot // self.strike_step) * self.strike_step

    def _itm_put_strike(self, spot: float) -> float:
        """Nearest strike ABOVE spot = ITM for puts."""
        base = (spot // self.strike_step) * self.strike_step
        return base if base >= spot else base + self.strike_step

    def _lookup_security_id(
        self, option_chain: dict[str, Any], strike: float, opt_type: str
    ) -> str:
        key   = str(int(strike))
        chain = option_chain.get("options_data", {}).get(key, {})
        if opt_type == "CE":
            return chain.get("call_options", {}).get("security_id", "")
        return chain.get("put_options", {}).get("security_id", "")
