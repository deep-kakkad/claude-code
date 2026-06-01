"""
EMA Crossover Call Buy Workflow.

Entry : 5 EMA crosses above 21 EMA on 5-min NIFTY candles.
Action: Buy next-expiry ITM CE (nearest strike below spot).
Exit  : Take-profit OR stop-loss on option premium, checked every 5 min.

Default exits:
  - Take-profit : +50% on premium  (e.g. buy @ 100, exit @ 150)
  - Stop-loss   : -30% on premium  (e.g. buy @ 100, exit @  70)

Schedule this to run every 5 minutes during market hours.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from config import load_config
from data import MarketData
from orders import OrderManager
from risk import RiskManager, PositionTracker
from signals.ema_crossover import EmaCrossoverStrategy
from signals.strategy import Signal, TradeSignal

logger = logging.getLogger(__name__)

NIFTY_SECURITY_ID = "13"
NIFTY_SEGMENT     = "NSE_IDX"
NIFTY_INSTRUMENT  = "INDEX"


class EmaCrossoverWorkflow:
    def __init__(
        self,
        quantity: int = 75,
        dry_run: bool = True,
        candle_interval: int = 5,
        lookback_days: int = 2,
        take_profit_pct: float = 0.50,   # exit when option gains 50%
        stop_loss_pct: float = 0.30,     # exit when option loses 30%
    ):
        self.quantity = quantity
        self.candle_interval = candle_interval
        self.lookback_days = lookback_days
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct

        cfg = load_config()
        self._market   = MarketData(cfg)
        tracker        = PositionTracker()
        risk           = RiskManager(cfg, tracker)
        self._orders   = OrderManager(cfg, risk, dry_run=dry_run)
        self._strategy = EmaCrossoverStrategy()

    def run(self):
        logger.info("=== EMA Crossover Workflow ===")

        open_positions = self._orders._risk.tracker.open_positions

        # ── 1. Check exits on open position ──────────────────────────────────
        if open_positions:
            self._check_exits(open_positions)
            return   # don't look for new entries while in a trade

        # ── 2. Look for entry signal ──────────────────────────────────────────
        candles = self._fetch_5min_candles()
        if len(candles) < 22:
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
            logger.error("Could not find security_id for %.0f CE in option chain", cs.strike)
            return

        logger.info("ENTRY SIGNAL: %s", cs.reason)
        ltp = self._get_ltp(cs.security_id)
        if ltp <= 0:
            logger.error("Invalid LTP for %s", cs.security_id)
            return

        limit_price = round(ltp * 1.002, 1)   # tiny buffer above LTP for fill
        signal = self._strategy.to_trade_signal(cs)
        resp = self._orders.execute_signal(signal, self.quantity, limit_price, confirm=False)

        if resp and resp.get("status") in ("success", "simulated"):
            # Record entry premium for SL/TP tracking
            pos = self._orders._risk.tracker.open_positions
            if pos:
                pos[-1]["entry_premium"] = limit_price
            logger.info(
                "Entered: %.0f CE @ %.1f  |  TP=%.1f  SL=%.1f",
                cs.strike, limit_price,
                limit_price * (1 + self.take_profit_pct),
                limit_price * (1 - self.stop_loss_pct),
            )

    # ── Exit logic ────────────────────────────────────────────────────────────

    def _check_exits(self, positions: list[dict]):
        for pos in list(positions):
            current_ltp = self._get_ltp(pos["security_id"])
            if current_ltp <= 0:
                logger.warning("Could not get LTP for %s — skipping exit check", pos["security_id"])
                continue

            entry = pos.get("entry_premium", current_ltp)
            pnl_pct = (current_ltp - entry) / entry

            logger.info(
                "Position check: %s %.0f%s  entry=%.1f  ltp=%.1f  pnl=%.1f%%",
                pos.get("underlying", "NIFTY"),
                pos.get("strike", 0),
                pos.get("option_type", "CE"),
                entry, current_ltp, pnl_pct * 100,
            )

            exit_reason = self._exit_reason(pnl_pct)
            if exit_reason:
                logger.info("EXIT triggered: %s (pnl=%.1f%%)", exit_reason, pnl_pct * 100)
                self._place_exit(pos, current_ltp, exit_reason)

    def _exit_reason(self, pnl_pct: float) -> Optional[str]:
        if pnl_pct >= self.take_profit_pct:
            return f"Take-profit hit ({pnl_pct:.1%})"
        if pnl_pct <= -self.stop_loss_pct:
            return f"Stop-loss hit ({pnl_pct:.1%})"
        return None

    def _place_exit(self, pos: dict, current_ltp: float, reason: str):
        # Sell slightly below LTP to improve fill on exit
        limit_price = round(current_ltp * 0.998, 1)
        exit_signal = TradeSignal(
            signal=Signal.EXIT,
            underlying=pos.get("underlying", "NIFTY"),
            strike=pos.get("strike", 0),
            option_type=pos.get("option_type", "CE"),
            expiry=pos.get("expiry", ""),
            security_id=pos["security_id"],
            reason=reason,
        )
        resp = self._orders.execute_signal(
            exit_signal, pos["quantity"], limit_price, confirm=False
        )
        logger.info("Exit order: %s", resp)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_ltp(self, security_id: str) -> float:
        ltp_map = self._market.get_ltp([{
            "exchange_segment": "NSE_FNO",
            "security_id": security_id,
            "instrument_type": "OPTIDX",
        }])
        return ltp_map.get(security_id, 0.0)

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
        expiries = self._market.get_expiry_list("NIFTY")
        today = date.today().isoformat()
        future = sorted(e for e in expiries if e > today)
        return future[1] if len(future) > 1 else (future[0] if future else None)
