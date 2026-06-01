"""
NIFTY 5-min candle feed via Yahoo Finance (free, no subscription needed).
Used for EMA signal generation only — orders still go through DhanHQ.
"""
from __future__ import annotations

import logging
from datetime import datetime

import yfinance as yf

logger = logging.getLogger(__name__)

NIFTY_TICKER = "^NSEI"


def get_nifty_candles(interval: str = "5m", period: str = "5d") -> list[dict]:
    """
    Fetch NIFTY 5-min candles from Yahoo Finance.
    Returns list of {"open","high","low","close","timestamp"} dicts,
    excluding the current (incomplete) candle.
    """
    df = yf.download(NIFTY_TICKER, period=period, interval=interval, progress=False)

    if df.empty:
        logger.error("yfinance returned empty dataframe for %s", NIFTY_TICKER)
        return []

    # Drop incomplete last candle
    df = df.iloc[:-1]

    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "timestamp": str(ts),
            "open":  float(row["Open"][NIFTY_TICKER]),
            "high":  float(row["High"][NIFTY_TICKER]),
            "low":   float(row["Low"][NIFTY_TICKER]),
            "close": float(row["Close"][NIFTY_TICKER]),
        })

    logger.info("Fetched %d NIFTY 5-min candles (last: %s close=%.2f)",
                len(candles), candles[-1]["timestamp"], candles[-1]["close"])
    return candles


def get_nifty_spot() -> float:
    """Get the latest NIFTY spot price."""
    candles = get_nifty_candles(interval="1m", period="1d")
    return candles[-1]["close"] if candles else 0.0
