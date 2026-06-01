"""
EMA Crossover Call Buy Workflow.

Triggers on: 5 EMA crosses above 21 EMA on 5-min NIFTY candles.
Action     : Buy 1 ITM CE of next weekly expiry.
Exit       : Manual / time-based (add stop-loss logic as needed).

Schedule this to run every 5 minutes during market hours.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from config import load_config
from data import MarketData
from orders import OrderManager
from risk import RiskManager, PositionTracker
from signals.ema_crossover import EmaCrossoverStrategy

logger = logging.getLogger(__name__)

# NIFTY index security_id on NSE_IDX segment
NIFTY_SECURITY_ID = "13"
NIFTY_SEGMENT     = "NSE_IDX"
NIFTY_INSTRUMENT  = "INDEX"


class EmaCrossoverWorkflow:
    def __init__(
        self,
        quantity: int = 75,       # 1 lot of NIFTY
        dry_run: bool = True,
        candle_interval: int = 5, # minutes
        lookback_days: int = 2,   # fetch last N days of intraday data
    ):
        self.quantity = quantity
        self.candle_interval = candle_interval
        self.lookback_days = lookback_days

        cfg = load_config()
        self._market   = MarketData(cfg)
        tracker        = PositionTracker()
        risk           = RiskManager(cfg, tracker)
        self._orders   = OrderManager(cfg, risk, dry_run=dry_run)
        self._strategy = EmaCrossoverStrategy()

    def run(self):
        logger.info("=== EMA Crossover Workflow ===")

        # Skip if already in a trade
        if self._orders._risk.tracker.open_positions:
            logger.info("Position already open — skipping entry check")
            return

        candles = self._fetch_5min_candles()
        if len(candles) < 22:   # need at least 21 bars for 21 EMA to warm up
            logger.warning("Insufficient candles (%d) — need 22+", len(candles))
            return

        expiry = self._get_next_expiry()
        if not expiry:
            logger.warning("No expiry available")
            return

        option_chain = self._market.get_option_chain("NIFTY", expiry)

        cs = self._strategy.evaluate(candles, option_chain, expiry)

        if not cs.fired:
            logger.info("No signal. spot=%.2f 5EMA=%.2f 21EMA=%.2f", cs.spot, cs.ema5, cs.ema21)
            return

        if not cs.security_id:
            logger.error("Could not find security_id for %s CE in option chain", cs.strike)
            return

        logger.info("SIGNAL: %s", cs.reason)

        # Get current LTP of the option to use as limit price
        ltp_map = self._market.get_ltp([{
            "exchange_segment": "NSE_FNO",
            "security_id": cs.security_id,
            "instrument_type": "OPTIDX",
        }])
        ltp = ltp_map.get(cs.security_id, 0.0)

        if ltp <= 0:
            logger.error("Invalid LTP %.2f for security %s", ltp, cs.security_id)
            return

        # Place limit order slightly above LTP to improve fill probability
        limit_price = round(ltp * 1.002, 1)   # 0.2% above LTP

        signal = self._strategy.to_trade_signal(cs)
        resp = self._orders.execute_signal(signal, self.quantity, limit_price)
        logger.info("Order response: %s", resp)

    def _fetch_5min_candles(self) -> list[dict]:
        today     = date.today().isoformat()
        from_date = (date.today() - timedelta(days=self.lookback_days)).isoformat()
        return self._market.get_intraday_ohlc(
            security_id=NIFTY_SECURITY_ID,
            exchange_segment=NIFTY_SEGMENT,
            instrument_type=NIFTY_INSTRUMENT,
            interval=self.candle_interval,
            from_date=from_date,
            to_date=today,
        )

    def _get_next_expiry(self) -> str | None:
        """Return the second-nearest expiry (next expiry after the current one)."""
        expiries = self._market.get_expiry_list("NIFTY")
        today = date.today().isoformat()
        future = sorted(e for e in expiries if e > today)
        # index 0 = current/nearest expiry, index 1 = NEXT expiry
        return future[1] if len(future) > 1 else (future[0] if future else None)
