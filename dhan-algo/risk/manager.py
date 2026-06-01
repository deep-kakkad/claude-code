"""
Risk manager — validates orders before they reach the exchange.
All checks must pass; any failure raises RiskViolation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config import DhanConfig

logger = logging.getLogger(__name__)

# NSE F&O lot sizes (update when exchange revises)
LOT_SIZES: dict[str, int] = {
    "NIFTY": 75,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
    "MIDCPNIFTY": 75,
    "SENSEX": 10,
}


class RiskViolation(Exception):
    pass


@dataclass
class PositionTracker:
    daily_pnl: float = 0.0
    open_positions: list[dict] = field(default_factory=list)

    def add_position(self, pos: dict):
        self.open_positions.append(pos)

    def remove_position(self, security_id: str):
        self.open_positions = [p for p in self.open_positions if p["security_id"] != security_id]

    def update_pnl(self, pnl_delta: float):
        self.daily_pnl += pnl_delta

    def reset_daily(self):
        self.daily_pnl = 0.0


class RiskManager:
    def __init__(self, cfg: DhanConfig, tracker: PositionTracker | None = None):
        self._cfg = cfg
        self.tracker = tracker or PositionTracker()

    def validate_order(
        self,
        underlying: str,
        security_id: str,
        quantity: int,
        order_type: str,
        price: float,
        transaction_type: str,
    ) -> None:
        """Raise RiskViolation if the order fails any risk check."""
        self._check_lot_size(underlying, quantity)
        self._check_open_positions()
        self._check_daily_loss()
        self._check_order_type(order_type)
        logger.info(
            "Risk OK: %s %s %s qty=%d @ %.2f",
            transaction_type, underlying, security_id, quantity, price,
        )

    def _check_lot_size(self, underlying: str, quantity: int):
        lot = LOT_SIZES.get(underlying.upper())
        if lot and quantity % lot != 0:
            raise RiskViolation(
                f"Quantity {quantity} is not a multiple of lot size {lot} for {underlying}"
            )

    def _check_open_positions(self):
        if len(self.tracker.open_positions) >= self._cfg.max_open_positions:
            raise RiskViolation(
                f"Max open positions ({self._cfg.max_open_positions}) reached"
            )

    def _check_daily_loss(self):
        if self.tracker.daily_pnl <= -abs(self._cfg.max_daily_loss):
            raise RiskViolation(
                f"Daily loss limit ₹{self._cfg.max_daily_loss} breached "
                f"(current P&L: ₹{self.tracker.daily_pnl:.2f})"
            )

    def _check_order_type(self, order_type: str):
        # Disallow naked MARKET orders for options
        if order_type == "MARKET":
            raise RiskViolation(
                "MARKET orders are disabled — use LIMIT to control slippage"
            )
