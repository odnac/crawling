# market_data.py
import time
import requests
from typing import Optional

BINANCE_PRICE_API_URL = "https://api.binance.com/api/v3/ticker/price"


def get_binance_price(
    symbol: str, max_retries: int = 5, base_delay: float = 0.5
) -> float:
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(
                BINANCE_PRICE_API_URL, params={"symbol": symbol}, timeout=10
            )
            r.raise_for_status()
            return float(r.json()["price"])

        except (requests.RequestException, ValueError) as e:
            last_exc = e
            if attempt == max_retries:
                break

            sleep_sec = base_delay * (2 ** (attempt - 1))
            print(
                f"[WARN] Binance price fetch failed "
                f"(attempt {attempt}/{max_retries}) → retry in {sleep_sec:.1f}s"
            )
            time.sleep(sleep_sec)

    raise RuntimeError(
        f"Binance price fetch failed after {max_retries} retries"
    ) from last_exc
