"""
Technical indicator calculations on OHLC data.
All functions operate on plain lists — no pandas dependency required.
"""
from __future__ import annotations


def rsi(closes: list[float], period: int = 14) -> float:
    """Relative Strength Index (last value)."""
    if len(closes) < period + 1:
        return 50.0  # neutral when insufficient data

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    if not values:
        return []
    k = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def sma(values: list[float], period: int) -> float:
    """Simple moving average (last value)."""
    if len(values) < period:
        return values[-1] if values else 0.0
    return sum(values[-period:]) / period


def iv_rank(current_iv: float, iv_history: list[float]) -> float:
    """
    IV Rank: where current IV sits relative to 52-week high/low range.
    Returns 0–100.
    """
    if not iv_history:
        return 50.0
    low, high = min(iv_history), max(iv_history)
    if high == low:
        return 50.0
    return (current_iv - low) / (high - low) * 100


def crossed_above(fast: list[float], slow: list[float]) -> bool:
    """
    Returns True if fast crossed above slow on the last completed candle.
    Requires at least 2 values in each list.
    Condition: fast[-2] <= slow[-2]  AND  fast[-1] > slow[-1]
    """
    if len(fast) < 2 or len(slow) < 2:
        return False
    return fast[-2] <= slow[-2] and fast[-1] > slow[-1]


def price_above_both(price: float, ema_fast: list[float], ema_slow: list[float]) -> bool:
    """True when price is above both EMAs (trend confirmation)."""
    return bool(ema_fast and ema_slow and price > ema_fast[-1] and price > ema_slow[-1])


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range — used for dynamic stop-loss sizing."""
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / min(len(trs), period)
