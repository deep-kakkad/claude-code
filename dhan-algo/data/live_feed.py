"""
WebSocket live feed — subscribe to real-time option price ticks.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

from dhanhq import DhanContext, marketfeed

from config import DhanConfig

logger = logging.getLogger(__name__)

# Callback type: (security_id, tick_data) -> None
TickCallback = Callable[[str, dict], None]


class LiveFeed:
    def __init__(self, cfg: DhanConfig):
        self._ctx = DhanContext(cfg.client_id, cfg.access_token)
        self._callbacks: dict[str, list[TickCallback]] = defaultdict(list)
        self._feed: marketfeed.DhanFeed | None = None
        self._thread: threading.Thread | None = None

    def subscribe(self, security_id: str, exchange_segment: str, callback: TickCallback):
        """Register a callback for ticks on a security."""
        self._callbacks[security_id].append(callback)
        logger.info("Subscribed to %s / %s", exchange_segment, security_id)

    def start(self, instruments: list[tuple[str, str, int]]):
        """
        Start the WebSocket feed.
        instruments: list of (exchange_segment, security_id, subscription_type)
          subscription_type: 15=quote, 17=full packet
        """
        def _on_message(data: dict):
            sid = str(data.get("security_id", ""))
            for cb in self._callbacks.get(sid, []):
                try:
                    cb(sid, data)
                except Exception:
                    logger.exception("Callback error for %s", sid)

        self._feed = marketfeed.DhanFeed(
            self._ctx,
            instruments,
            marketfeed.Quote,  # subscription mode
            on_message=_on_message,
        )
        self._thread = threading.Thread(target=self._feed.run_forever, daemon=True)
        self._thread.start()
        logger.info("Live feed started with %d instruments", len(instruments))

    def stop(self):
        if self._feed:
            self._feed.close()
        logger.info("Live feed stopped")
