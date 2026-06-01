"""
Order manager — places, modifies, and cancels orders via DhanHQ SDK.
Wraps every order in a risk check and requires explicit confirmation.
"""
from __future__ import annotations

import logging
from typing import Any

from dhanhq import DhanContext, dhanhq

from config import DhanConfig
from risk import RiskManager, RiskViolation
from signals import TradeSignal, Signal

logger = logging.getLogger(__name__)

# NSE F&O segment constant
NSE_FNO = "NSE_FNO"


class OrderManager:
    def __init__(self, cfg: DhanConfig, risk: RiskManager, dry_run: bool = True):
        ctx = DhanContext(cfg.client_id, cfg.access_token)
        self._dhan = dhanhq(ctx)
        self._risk = risk
        self._dry_run = dry_run  # True = log only, don't hit exchange

    def execute_signal(
        self,
        signal: TradeSignal,
        quantity: int,
        price: float,
        product_type: str = "INTRADAY",
        confirm: bool = True,
    ) -> dict[str, Any] | None:
        """
        Execute a trade signal. Requires explicit confirmation unless confirm=False.
        Returns order response dict or None on failure.
        """
        if signal.signal == Signal.HOLD:
            return None

        transaction_type = "SELL" if signal.signal in (Signal.SELL, Signal.EXIT) else "BUY"
        # EXIT means buy-to-close a short position
        if signal.signal == Signal.EXIT:
            transaction_type = "BUY"

        try:
            self._risk.validate_order(
                underlying=signal.underlying,
                security_id=signal.security_id,
                quantity=quantity,
                order_type="LIMIT",
                price=price,
                transaction_type=transaction_type,
            )
        except RiskViolation as e:
            logger.error("Risk check failed: %s", e)
            return None

        logger.info(
            "[%s] %s %s %s qty=%d @ %.2f | %s",
            "DRY_RUN" if self._dry_run else "LIVE",
            transaction_type, signal.underlying,
            f"{signal.strike}{signal.option_type}",
            quantity, price, signal.reason,
        )

        if confirm and not self._dry_run:
            self._confirm_order(transaction_type, signal, quantity, price)

        if self._dry_run:
            return {"status": "simulated", "signal": signal, "price": price, "qty": quantity}

        resp = self._dhan.place_order(
            security_id=signal.security_id,
            exchange_segment=NSE_FNO,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type="LIMIT",
            product_type=product_type,
            price=price,
        )

        if resp.get("status") == "success":
            if signal.signal == Signal.SELL:
                self._risk.tracker.add_position({
                    "security_id": signal.security_id,
                    "underlying": signal.underlying,
                    "strike": signal.strike,
                    "option_type": signal.option_type,
                    "expiry": signal.expiry,
                    "entry_premium": price,
                    "quantity": quantity,
                })
            elif signal.signal == Signal.EXIT:
                self._risk.tracker.remove_position(signal.security_id)

        return resp

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        logger.info("Cancelling order %s", order_id)
        if self._dry_run:
            return {"status": "simulated_cancel", "order_id": order_id}
        return self._dhan.cancel_order(order_id=order_id)

    def get_positions(self) -> list[dict]:
        resp = self._dhan.get_positions()
        return resp.get("data", []) if resp.get("status") == "success" else []

    def get_order_book(self) -> list[dict]:
        resp = self._dhan.get_order_list()
        return resp.get("data", []) if resp.get("status") == "success" else []

    def _confirm_order(self, txn: str, signal: TradeSignal, qty: int, price: float):
        """Block until user types 'yes' to confirm live order."""
        prompt = (
            f"\n{'='*50}\n"
            f"CONFIRM LIVE ORDER\n"
            f"  Action : {txn}\n"
            f"  Symbol : {signal.underlying} {signal.strike}{signal.option_type} {signal.expiry}\n"
            f"  Qty    : {qty}\n"
            f"  Price  : {price:.2f}\n"
            f"  Reason : {signal.reason}\n"
            f"{'='*50}\n"
            f"Type 'yes' to confirm: "
        )
        answer = input(prompt).strip().lower()
        if answer != "yes":
            raise RuntimeError("Order cancelled by user")
