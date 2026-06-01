"""
EMA Crossover Strategy for NIFTY F&O call buying.

Entry rule (all must be true on the last closed 5-min candle):
  1. NIFTY close > 5 EMA  (price above fast EMA)
  2. NIFTY close > 21 EMA (price above slow EMA)
  3. 5 EMA crossed above 21 EMA on this candle   ← the trigger

On signal: buy 1 ITM call of the NEXT weekly expiry.
ITM strike = nearest strike BELOW spot (one strike in-the-money).

One trade at a time — no new entry if a position is already open.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .indicators import ema, crossed_above
from .strategy import Signal, TradeSignal

logger = logging.getLogger(__name__)

# NIFTY strikes are spaced 50 points apart
NIFTY_STRIKE_STEP = 50


@dataclass
class CrossoverSignal:
    fired: bool
    spot: float
    ema5: float
    ema21: float
    strike: float
    expiry: str
    security_id: str
    reason: str


class EmaCrossoverStrategy:
    """
    Detects 5 EMA crossing above 21 EMA on 5-min NIFTY candles,
    then returns the ITM call details to buy.
    """

    def __init__(self, fast: int = 5, slow: int = 21, strike_step: int = NIFTY_STRIKE_STEP):
        self.fast = fast
        self.slow = slow
        self.strike_step = strike_step

    def evaluate(
        self,
        candles: list[dict],           # list of {"open","high","low","close","timestamp"}
        option_chain: dict[str, Any],  # from MarketData.get_option_chain
        expiry: str,
    ) -> CrossoverSignal:
        """
        Evaluate latest candles and return a CrossoverSignal.
        candles must contain at least `slow` + 1 bars.
        """
        closes = [c["close"] for c in candles]
        spot = closes[-1]

        ema5  = ema(closes, self.fast)
        ema21 = ema(closes, self.slow)

        fired = crossed_above(ema5, ema21)

        logger.info(
            "NIFTY spot=%.2f  5EMA=%.2f  21EMA=%.2f  crossover=%s",
            spot, ema5[-1], ema21[-1], fired,
        )

        if not fired:
            return CrossoverSignal(
                fired=False, spot=spot,
                ema5=ema5[-1], ema21=ema21[-1],
                strike=0, expiry=expiry, security_id="", reason="No crossover",
            )

        strike = self._itm_call_strike(spot)
        security_id = self._lookup_security_id(option_chain, strike, "CE")

        reason = (
            f"5EMA({ema5[-1]:.1f}) crossed above 21EMA({ema21[-1]:.1f}), "
            f"spot={spot:.1f}, ITM strike={strike}"
        )

        return CrossoverSignal(
            fired=True, spot=spot,
            ema5=ema5[-1], ema21=ema21[-1],
            strike=strike, expiry=expiry,
            security_id=security_id, reason=reason,
        )

    def to_trade_signal(self, cs: CrossoverSignal) -> TradeSignal:
        return TradeSignal(
            signal=Signal.BUY,
            underlying="NIFTY",
            strike=cs.strike,
            option_type="CE",
            expiry=cs.expiry,
            security_id=cs.security_id,
            reason=cs.reason,
        )

    def _itm_call_strike(self, spot: float) -> float:
        """Nearest strike below spot = ITM call strike."""
        return (spot // self.strike_step) * self.strike_step

    def _lookup_security_id(
        self, option_chain: dict[str, Any], strike: float, opt_type: str
    ) -> str:
        """Extract security_id for the given strike and option type from option chain."""
        key = str(int(strike))
        chain = option_chain.get("options_data", {}).get(key, {})
        if opt_type == "CE":
            return chain.get("call_options", {}).get("security_id", "")
        return chain.get("put_options", {}).get("security_id", "")
