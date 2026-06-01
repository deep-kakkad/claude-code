"""
EMA Crossover Call Buy Workflow.

Entry : 5 EMA crosses above 21 EMA on 5-min NIFTY candles (Yahoo Finance).
Action: Buy next-expiry ITM CE via DhanHQ (strike lookup from instruments CSV).
Exit  : Take-profit OR stop-loss on option premium, checked every 5 min.

Defaults:  TP = +50%  |  SL = -30%
"""
from __future__ import annotations

import logging
from typing import Optional

from config import load_config
from data.nifty_feed import get_nifty_candles
from data.instruments import get_nifty_expiries, get_itm_call, lot_size
from orders import OrderManager
from risk import RiskManager, PositionTracker
from signals.ema_crossover import EmaCrossoverStrategy
from signals.strategy import Signal, TradeSignal

logger = logging.getLogger(__name__)


class EmaCrossoverWorkflow:
    def __init__(
        self,
        quantity: int | None = None,   # None = auto (1 lot from instruments CSV)
        dry_run: bool = True,
        take_profit_pct: float = 0.50,
        stop_loss_pct: float = 0.30,
    ):
        self._quantity_override = quantity
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct

        cfg = load_config()
        tracker        = PositionTracker()
        risk           = RiskManager(cfg, tracker)
        self._orders   = OrderManager(cfg, risk, dry_run=dry_run)
        self._strategy = EmaCrossoverStrategy()

        from dhanhq import DhanContext, dhanhq
        from dhanhq import marketfeed
        ctx = DhanContext(cfg.client_id, cfg.access_token)
        self._dhan = dhanhq(ctx)

    def run(self):
        logger.info("=== EMA Crossover Workflow ===")

        open_positions = self._orders._risk.tracker.open_positions

        # ── 1. Check exits on open position ──────────────────────────────────
        if open_positions:
            self._check_exits(open_positions)
            return

        # ── 2. Fetch 5-min NIFTY candles (Yahoo Finance) ──────────────────────
        candles = get_nifty_candles(interval="5m", period="5d")
        if len(candles) < 22:
            logger.warning("Insufficient candles (%d) — need 22+", len(candles))
            return

        spot = candles[-1]["close"]
        logger.info("NIFTY spot: %.2f", spot)

        # ── 3. Determine next expiry ──────────────────────────────────────────
        expiries = get_nifty_expiries()
        if len(expiries) < 2:
            logger.warning("Not enough expiries found")
            return
        expiry = expiries[1]   # skip nearest, use next weekly
        logger.info("Target expiry: %s", expiry)

        # ── 4. Find ITM CE instrument ─────────────────────────────────────────
        instrument = get_itm_call(spot, expiry)
        if not instrument:
            logger.error("Could not find ITM CE instrument for spot %.2f expiry %s", spot, expiry)
            return

        security_id = instrument["SEM_SMST_SECURITY_ID"]
        strike      = float(instrument["SEM_STRIKE_PRICE"])
        qty         = self._quantity_override or lot_size(instrument)

        logger.info("ITM CE: %s  security_id=%s  lot=%d",
                    instrument["SEM_TRADING_SYMBOL"], security_id, qty)

        # ── 5. Evaluate EMA crossover signal ──────────────────────────────────
        # Build a minimal option_chain dict for strategy (just needs security_id)
        mock_chain = {
            "options_data": {
                str(int(strike)): {
                    "call_options": {"security_id": security_id},
                }
            }
        }
        cs = self._strategy.evaluate(candles, mock_chain, expiry)

        if not cs.fired:
            logger.info("No signal | spot=%.2f  5EMA=%.2f  21EMA=%.2f",
                        cs.spot, cs.ema5, cs.ema21)
            return

        logger.info("ENTRY SIGNAL: %s", cs.reason)

        # ── 6. Get option LTP for limit price ─────────────────────────────────
        ltp = self._get_ltp(security_id)
        if ltp <= 0:
            logger.error("Could not get LTP for %s — cannot place order", security_id)
            return

        limit_price = round(ltp * 1.002, 1)   # tiny buffer for fill
        signal = TradeSignal(
            signal=Signal.BUY,
            underlying="NIFTY",
            strike=strike,
            option_type="CE",
            expiry=expiry,
            security_id=security_id,
            reason=cs.reason,
        )

        resp = self._orders.execute_signal(signal, qty, limit_price, confirm=False)

        if resp and resp.get("status") in ("success", "simulated"):
            pos = self._orders._risk.tracker.open_positions
            if pos:
                pos[-1]["entry_premium"] = limit_price
                pos[-1]["underlying"]    = "NIFTY"
                pos[-1]["strike"]        = strike
                pos[-1]["option_type"]   = "CE"
                pos[-1]["expiry"]        = expiry
            logger.info(
                "Entered: %s @ ₹%.1f  |  TP @ ₹%.1f (+%.0f%%)  SL @ ₹%.1f (-%.0f%%)",
                instrument["SEM_TRADING_SYMBOL"], limit_price,
                limit_price * (1 + self.take_profit_pct), self.take_profit_pct * 100,
                limit_price * (1 - self.stop_loss_pct),  self.stop_loss_pct  * 100,
            )

    # ── Exit logic ────────────────────────────────────────────────────────────

    def _check_exits(self, positions: list[dict]):
        for pos in list(positions):
            ltp = self._get_ltp(pos["security_id"])
            if ltp <= 0:
                logger.warning("No LTP for %s — skipping exit check", pos["security_id"])
                continue

            entry    = pos.get("entry_premium", ltp)
            pnl_pct  = (ltp - entry) / entry
            pnl_inr  = (ltp - entry) * pos.get("quantity", 1)

            logger.info(
                "Position: %s %.0f%s  entry=₹%.1f  ltp=₹%.1f  pnl=%+.1f%% (₹%+.0f)",
                pos.get("underlying", "NIFTY"), pos.get("strike", 0),
                pos.get("option_type", "CE"), entry, ltp, pnl_pct * 100, pnl_inr,
            )

            reason = self._exit_reason(pnl_pct)
            if reason:
                logger.info("EXIT triggered: %s", reason)
                self._place_exit(pos, ltp, reason)

    def _exit_reason(self, pnl_pct: float) -> Optional[str]:
        if pnl_pct >= self.take_profit_pct:
            return f"Take-profit hit ({pnl_pct:+.1%})"
        if pnl_pct <= -self.stop_loss_pct:
            return f"Stop-loss hit ({pnl_pct:+.1%})"
        return None

    def _place_exit(self, pos: dict, ltp: float, reason: str):
        limit_price = round(ltp * 0.998, 1)
        exit_signal = TradeSignal(
            signal=Signal.EXIT,
            underlying=pos.get("underlying", "NIFTY"),
            strike=pos.get("strike", 0),
            option_type=pos.get("option_type", "CE"),
            expiry=pos.get("expiry", ""),
            security_id=pos["security_id"],
            reason=reason,
        )
        self._orders.execute_signal(exit_signal, pos["quantity"], limit_price, confirm=False)

    def _get_ltp(self, security_id: str) -> float:
        resp = self._dhan.ticker_data({"NSE_FNO": [security_id]})
        if resp.get("status") != "success":
            return 0.0
        for item in (resp.get("data") or []):
            if str(item.get("security_id")) == str(security_id):
                return float(item.get("last_price", 0))
        return 0.0
