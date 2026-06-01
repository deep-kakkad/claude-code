"""
Runner: NIFTY 5EMA × 21EMA crossover → buy next-expiry ITM call.

Usage:
  # Load credentials from .env then run paper trading
  source .env && python run_ema_crossover.py

  # Live trading
  source .env && python run_ema_crossover.py --live

  # Custom SL/TP
  python run_ema_crossover.py --live --tp 0.60 --sl 0.25

  # 2 lots
  python run_ema_crossover.py --live --lots 2
"""
import argparse
import logging
import os
import time
from pathlib import Path


def _load_dotenv():
    """Load .env from the script's directory if it exists."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

from workflows.ema_crossover_buy import EmaCrossoverWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ema_runner")

NIFTY_LOT = 75
INTERVAL  = 5 * 60   # every 5 minutes

MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 25)   # stop 5 min before close


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
    parser.add_argument("--lots", type=int, default=1, help="Number of lots")
    parser.add_argument("--tp", type=float, default=0.50,
                        help="Take-profit %% of premium (default 0.50 = 50%%)")
    parser.add_argument("--sl", type=float, default=0.30,
                        help="Stop-loss %% of premium (default 0.30 = 30%%)")
    args = parser.parse_args()

    qty = args.lots * NIFTY_LOT

    logger.info("Strategy : NIFTY 5EMA × 21EMA crossover → buy next-expiry ITM CE")
    logger.info("Quantity : %d (%d lot)", qty, args.lots)
    logger.info("TP / SL  : +%.0f%% / -%.0f%%", args.tp * 100, args.sl * 100)
    logger.info("Mode     : %s", "LIVE" if args.live else "DRY RUN")

    workflow = EmaCrossoverWorkflow(
        quantity=qty,
        dry_run=not args.live,
        take_profit_pct=args.tp,
        stop_loss_pct=args.sl,
    )

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
