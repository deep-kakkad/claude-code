"""
EMA Crossover Workflow — NIFTY CE and PE.

Entry:
  Bullish: 21 EMA crosses ABOVE 51 EMA → buy next-expiry ITM CE
  Bearish: 21 EMA crosses BELOW 51 EMA → buy next-expiry ITM PE

Exit: Take-profit (+50%) or Stop-loss (-30%) on option premium.
Data: 5-min NIFTY candles via Yahoo Finance. Orders via DhanHQ.
"""
from __future__ import annotations

import logging
from typing import Optional

from config import load_config
from data.nifty_feed import get_nifty_candles
from data.instruments import get_nifty_expiries, find_option, get_itm_call, lot_size
from orders import OrderManager
from risk import RiskManager, PositionTracker
from signals.ema_crossover import EmaCrossoverStrategy
from signals.strategy import Signal, TradeSignal

logger = logging.getLogger(__name__)

NIFTY_STRIKE_STEP = 50


class EmaCrossoverWorkflow:
    def __init__(
        self,
        quantity: int | None = None,
        dry_run: bool = True,
        take_profit_pct: float = 0.50,
        stop_loss_pct: float = 0.30,
    ):
        self._quantity_override = quantity
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct

        cfg = load_config()
        tracker      = PositionTracker()
        risk         = RiskManager(cfg, tracker)
        self._orders = OrderManager(cfg, risk, dry_run=dry_run)
        self._strategy = EmaCrossoverStrategy()   # 21/51 EMA

        from dhanhq import DhanContext, dhanhq
        ctx = DhanContext(cfg.client_id, cfg.access_token)
        self._dhan = dhanhq(ctx)

    def run(self):
        logger.info("=== EMA Crossover Workflow (21/51) ===")

        open_positions = self._orders._risk.tracker.open_positions

        # ── 1. Monitor open position for SL/TP ───────────────────────────────
        if open_positions:
            self._check_exits(open_positions)
            return

        # ── 2. Fetch 5-min NIFTY candles ─────────────────────────────────────
        candles = get_nifty_candles(interval="5m", period="5d")
        if len(candles) < 52:   # need 51+ bars for 51 EMA to warm up
            logger.warning("Insufficient candles (%d) — need 52+", len(candles))
            return

        spot = candles[-1]["close"]
        logger.info("NIFTY spot: %.2f", spot)

        # ── 3. Determine next expiry ──────────────────────────────────────────
        expiries = get_nifty_expiries()
        if len(expiries) < 2:
            logger.warning("Not enough expiries found")
            return
        expiry = expiries[1]
        logger.info("Target expiry: %s", expiry)

        # ── 4. Evaluate EMA crossover signal ──────────────────────────────────
        cs = self._strategy.evaluate(candles, {}, expiry)

        if not cs.fired:
            logger.info("No signal | spot=%.2f  21EMA=%.2f  51EMA=%.2f",
                        cs.spot, cs.ema_fast, cs.ema_slow)
            return

        logger.info("SIGNAL: %s", cs.reason)

        # ── 5. Look up ITM instrument ─────────────────────────────────────────
        instrument = self._get_itm_instrument(spot, expiry, cs.direction)
        if not instrument:
            logger.error("No ITM %s instrument found for spot=%.0f expiry=%s",
                         cs.direction, spot, expiry)
            return

        security_id = instrument["SEM_SMST_SECURITY_ID"]
        strike      = float(instrument["SEM_STRIKE_PRICE"])
        qty         = self._quantity_override or lot_size(instrument)

        logger.info("ITM %s: %s  sid=%s  lot=%d",
                    cs.direction, instrument["SEM_TRADING_SYMBOL"], security_id, qty)

        # ── 6. Get LTP and place order ────────────────────────────────────────
        ltp = self._get_ltp(security_id)
        if ltp <= 0:
            logger.error("Invalid LTP for %s — skipping order", security_id)
            return

        limit_price = round(ltp * 1.002, 1)
        signal = TradeSignal(
            signal=Signal.BUY,
            underlying="NIFTY",
            strike=strike,
            option_type=cs.direction,
            expiry=expiry,
            security_id=security_id,
            reason=cs.reason,
        )

        resp = self._orders.execute_signal(signal, qty, limit_price, confirm=False)

        if resp and resp.get("status") in ("success", "simulated"):
            pos = self._orders._risk.tracker.open_positions
            if pos:
                pos[-1].update({
                    "entry_premium": limit_price,
                    "underlying":    "NIFTY",
                    "strike":        strike,
                    "option_type":   cs.direction,
                    "expiry":        expiry,
                })
            logger.info(
                "Entered: %s @ ₹%.1f  |  TP ₹%.1f (+%.0f%%)  SL ₹%.1f (-%.0f%%)",
                instrument["SEM_TRADING_SYMBOL"], limit_price,
                limit_price * (1 + self.take_profit_pct), self.take_profit_pct * 100,
                limit_price * (1 - self.stop_loss_pct),   self.stop_loss_pct  * 100,
            )

    # ── Exit logic ────────────────────────────────────────────────────────────

    def _check_exits(self, positions: list[dict]):
        for pos in list(positions):
            ltp = self._get_ltp(pos["security_id"])
            if ltp <= 0:
                logger.warning("No LTP for %s — skipping", pos["security_id"])
                continue

            entry   = pos.get("entry_premium", ltp)
            pnl_pct = (ltp - entry) / entry
            pnl_inr = (ltp - entry) * pos.get("quantity", 1)

            logger.info(
                "Position: %s %.0f%s  entry=₹%.1f  ltp=₹%.1f  pnl=%+.1f%% (₹%+.0f)",
                pos.get("underlying", "NIFTY"), pos.get("strike", 0),
                pos.get("option_type", "?"), entry, ltp, pnl_pct * 100, pnl_inr,
            )

            reason = self._exit_reason(pnl_pct)
            if reason:
                logger.info("EXIT: %s", reason)
                self._place_exit(pos, ltp, reason)

    def _exit_reason(self, pnl_pct: float) -> Optional[str]:
        if pnl_pct >= self.take_profit_pct:
            return f"Take-profit hit ({pnl_pct:+.1%})"
        if pnl_pct <= -self.stop_loss_pct:
            return f"Stop-loss hit ({pnl_pct:+.1%})"
        return None

    def _place_exit(self, pos: dict, ltp: float, reason: str):
        exit_signal = TradeSignal(
            signal=Signal.EXIT,
            underlying=pos.get("underlying", "NIFTY"),
            strike=pos.get("strike", 0),
            option_type=pos.get("option_type", "CE"),
            expiry=pos.get("expiry", ""),
            security_id=pos["security_id"],
            reason=reason,
        )
        self._orders.execute_signal(
            exit_signal, pos["quantity"], round(ltp * 0.998, 1), confirm=False
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_itm_instrument(self, spot: float, expiry: str, direction: str) -> dict | None:
        if direction == "CE":
            # ITM call = strike below spot
            strike = (spot // NIFTY_STRIKE_STEP) * NIFTY_STRIKE_STEP
        else:
            # ITM put = strike above spot
            base   = (spot // NIFTY_STRIKE_STEP) * NIFTY_STRIKE_STEP
            strike = base if base >= spot else base + NIFTY_STRIKE_STEP

        return find_option("NIFTY", strike, direction, expiry)

    def _get_ltp(self, security_id: str) -> float:
        resp = self._dhan.ticker_data({"NSE_FNO": [security_id]})
        if resp.get("status") != "success":
            return 0.0
        for item in (resp.get("data") or []):
            if str(item.get("security_id")) == str(security_id):
                return float(item.get("last_price", 0))
        return 0.0
