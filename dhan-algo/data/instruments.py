"""
NIFTY F&O instrument lookup from DhanHQ's free public instruments master CSV.
Downloads once per session and caches in memory.
"""
from __future__ import annotations

import csv
import io
import logging
import ssl
import urllib.request
from datetime import date
from functools import lru_cache

logger = logging.getLogger(__name__)

CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"


@lru_cache(maxsize=1)
def _load_instruments() -> list[dict]:
    """Download and cache the full instruments CSV."""
    logger.info("Downloading instruments master from DhanHQ...")
    ctx = ssl.create_default_context()
    import certifi
    ctx.load_verify_locations(certifi.where())
    with urllib.request.urlopen(CSV_URL, context=ctx) as r:
        content = r.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(content)))
    logger.info("Loaded %d instruments", len(rows))
    return rows


def get_nifty_expiries() -> list[str]:
    """Return sorted list of all future NIFTY option expiry dates (YYYY-MM-DD)."""
    rows = _load_instruments()
    today = date.today().isoformat()
    expiries = set()
    for r in rows:
        if r.get("SEM_INSTRUMENT_NAME") == "OPTIDX" and "NIFTY" in r.get("SEM_TRADING_SYMBOL", ""):
            exp = r.get("SEM_EXPIRY_DATE", "")[:10]
            if exp > today:
                expiries.add(exp)
    return sorted(expiries)


def find_option(
    underlying: str,
    strike: float,
    option_type: str,   # "CE" or "PE"
    expiry: str,        # "YYYY-MM-DD"
) -> dict | None:
    """
    Look up an option contract and return its full instrument row.
    Returns None if not found.
    """
    rows = _load_instruments()
    strike_str = f"{strike:.5f}"

    for r in rows:
        if (
            r.get("SEM_INSTRUMENT_NAME") == "OPTIDX"
            and underlying in r.get("SEM_TRADING_SYMBOL", "")
            and r.get("SEM_OPTION_TYPE") == option_type
            and r.get("SEM_EXPIRY_DATE", "")[:10] == expiry
            and r.get("SEM_STRIKE_PRICE", "") == strike_str
        ):
            return r
    return None


def get_itm_call(spot: float, expiry: str, strike_step: int = 50) -> dict | None:
    """
    Find the ITM call for NIFTY: nearest strike BELOW spot for the given expiry.
    Returns the instrument row dict or None.
    """
    itm_strike = (spot // strike_step) * strike_step
    result = find_option("NIFTY", itm_strike, "CE", expiry)
    if not result:
        logger.warning("No instrument found for NIFTY %.0f CE %s", itm_strike, expiry)
    return result


def lot_size(instrument: dict) -> int:
    return int(float(instrument.get("SEM_LOT_UNITS", 65)))
