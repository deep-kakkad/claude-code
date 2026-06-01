"""
Short Straddle Workflow — end-to-end orchestrator.

Run once per scheduled interval (e.g., every 15 minutes):
  1. Fetch option chain
  2. Fetch historical closes for RSI
  3. Generate signals
  4. Check exit conditions on existing positions
  5. Execute new entries (if any)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from config import DhanConfig, load_config
from data import MarketData
from orders import OrderManager
from risk import RiskManager, PositionTracker
from signals import ShortStraddleStrategy

logger = logging.getLogger(__name__)


class ShortStraddleWorkflow:
    def __init__(
        self,
        underlying: str = "NIFTY",
        quantity: int = 75,            # 1 lot of NIFTY
        dry_run: bool = True,
        iv_rank_threshold: float = 70.0,
        profit_target_pct: float = 0.50,
        stop_loss_pct: float = 1.00,
    ):
        self.underlying = underlying
        self.quantity = quantity

        cfg = load_config()
        self._market = MarketData(cfg)
        tracker = PositionTracker()
        risk = RiskManager(cfg, tracker)
        self._orders = OrderManager(cfg, risk, dry_run=dry_run)
        self._strategy = ShortStraddleStrategy(
            iv_rank_threshold=iv_rank_threshold,
            profit_target_pct=profit_target_pct,
            stop_loss_pct=stop_loss_pct,
        )

    def run(self):
        """Main workflow — call this on a schedule."""
        logger.info("=== Short Straddle Workflow: %s ===", self.underlying)

        expiry = self._get_nearest_expiry()
        if not expiry:
            logger.warning("No expiry found for %s", self.underlying)
            return

        option_chain = self._market.get_option_chain(self.underlying, expiry)
        closes = self._get_recent_closes()
        iv_history = self._get_iv_history()

        # 1. Check exits on open positions
        for pos in list(self._orders._risk.tracker.open_positions):
            ltp = self._market.get_ltp([{
                "exchange_segment": "NSE_FNO",
                "security_id": pos["security_id"],
                "instrument_type": "OPTIDX",
            }])
            current_premium = ltp.get(pos["security_id"], pos["entry_premium"])
            exit_signal = self._strategy.check_exit(pos, current_premium)
            if exit_signal:
                logger.info("Exit signal: %s", exit_signal.reason)
                self._orders.execute_signal(exit_signal, pos["quantity"], current_premium)

        # 2. Enter new positions if no open trade
        if len(self._orders._risk.tracker.open_positions) == 0:
            signals = self._strategy.generate_entry_signals(
                self.underlying, expiry, option_chain, closes, iv_history
            )
            for sig in signals:
                spot = closes[-1] if closes else 0
                # Price at mid of bid-ask — use LTP as proxy here
                ltp = self._market.get_ltp([{
                    "exchange_segment": "NSE_FNO",
                    "security_id": sig.security_id,
                    "instrument_type": "OPTIDX",
                }])
                price = ltp.get(sig.security_id, 0)
                if price > 0:
                    self._orders.execute_signal(sig, self.quantity, price)

    def _get_nearest_expiry(self) -> str | None:
        expiries = self._market.get_expiry_list(self.underlying)
        today = date.today().isoformat()
        future = [e for e in expiries if e >= today]
        return future[0] if future else None

    def _get_recent_closes(self, days: int = 30) -> list[float]:
        today = date.today()
        from_date = (today - timedelta(days=days)).isoformat()
        # Use NIFTY index security_id = 13 on NSE_IDX
        bars = self._market.get_historical_ohlc(
            security_id="13",
            exchange_segment="NSE_IDX",
            instrument_type="INDEX",
            from_date=from_date,
            to_date=today.isoformat(),
        )
        return [b["close"] for b in bars if "close" in b]

    def _get_iv_history(self) -> list[float]:
        # Placeholder — in production, persist daily ATM IV to a DB/file
        # and load last 52 weeks here
        return []
