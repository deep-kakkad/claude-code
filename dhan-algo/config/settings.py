"""
Configuration management — load credentials from environment variables.
Never hardcode CLIENT_ID or ACCESS_TOKEN in source.
"""
import os
from dataclasses import dataclass


@dataclass
class DhanConfig:
    client_id: str
    access_token: str
    base_url: str = "https://api.dhan.co/v2"
    ws_url: str = "wss://api-feed.dhan.co"

    # Rate limits (requests per second)
    rate_limit_per_second: int = 10
    rate_limit_per_minute: int = 250

    # Risk defaults
    max_daily_loss: float = 5000.0       # INR
    max_open_positions: int = 5
    default_order_type: str = "LIMIT"    # never MARKET by default


def load_config() -> DhanConfig:
    client_id = os.environ.get("DHAN_CLIENT_ID")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN")

    if not client_id or not access_token:
        raise EnvironmentError(
            "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN environment variables."
        )

    return DhanConfig(client_id=client_id, access_token=access_token)
