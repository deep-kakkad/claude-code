"""
Runner: NIFTY 5EMA × 21EMA crossover → buy next-expiry ITM call.

Usage:
  # Paper trading (safe default)
  DHAN_CLIENT_ID=xxx DHAN_ACCESS_TOKEN=yyy python run_ema_crossover.py

  # Live trading
  DHAN_CLIENT_ID=xxx DHAN_ACCESS_TOKEN=yyy python run_ema_crossover.py --live

  # Custom lot count
  python run_ema_crossover.py --lots 2
"""
import argparse
import logging
import time

from workflows.ema_crossover_buy import EmaCrossoverWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ema_runner")

NIFTY_LOT = 75
INTERVAL  = 5 * 60  # run every 5 minutes (aligned to candle close)

MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 25)   # stop 5 min before close to avoid last-minute fills


def is_market_hours() -> bool:
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def main():
    parser = argparse.ArgumentParser(description="NIFTY EMA Crossover Call Buyer")
    parser.add_argument("--live", action="store_true", help="Place real orders")
    parser.add_argument("--lots", type=int, default=1, help="Number of lots to buy")
    args = parser.parse_args()

    qty = args.lots * NIFTY_LOT

    logger.info(
        "Strategy : NIFTY 5EMA × 21EMA crossover → buy next-expiry ITM CE"
    )
    logger.info("Quantity : %d (%d lot)", qty, args.lots)
    logger.info("Mode     : %s", "LIVE" if args.live else "DRY RUN")

    workflow = EmaCrossoverWorkflow(quantity=qty, dry_run=not args.live)

    while True:
        if is_market_hours():
            try:
                workflow.run()
            except Exception:
                logger.exception("Workflow error — will retry next candle")
        else:
            logger.info("Outside market hours — sleeping")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
