"""
Entry point — run the Short Straddle workflow on a schedule.

Usage:
  # Dry run (paper trading, no real orders)
  python main.py

  # Live trading (real orders, confirmation prompt before each order)
  DHAN_CLIENT_ID=xxx DHAN_ACCESS_TOKEN=yyy python main.py --live

  # Custom interval
  python main.py --interval 900   # every 15 minutes
"""
import argparse
import logging
import time

from workflows import ShortStraddleWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

MARKET_OPEN  = (9, 15)   # 09:15 IST
MARKET_CLOSE = (15, 30)  # 15:30 IST


def is_market_hours() -> bool:
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def main():
    parser = argparse.ArgumentParser(description="Dhan F&O Algo")
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    parser.add_argument("--interval", type=int, default=900, help="Check interval in seconds")
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--quantity", type=int, default=75, help="Quantity (must be 1 lot minimum)")
    parser.add_argument("--iv-rank", type=float, default=70.0, help="IV Rank threshold for entry")
    args = parser.parse_args()

    if args.live:
        logger.warning("LIVE TRADING MODE — real orders will be placed after confirmation")
    else:
        logger.info("DRY RUN mode — no real orders")

    workflow = ShortStraddleWorkflow(
        underlying=args.underlying,
        quantity=args.quantity,
        dry_run=not args.live,
        iv_rank_threshold=args.iv_rank,
    )

    logger.info("Starting scheduler — interval=%ds", args.interval)

    while True:
        if is_market_hours():
            try:
                workflow.run()
            except Exception:
                logger.exception("Workflow error — will retry next interval")
        else:
            logger.info("Outside market hours — sleeping")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
