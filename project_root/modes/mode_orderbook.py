# orderbook_mode.py
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from modes.utils_driver import init_driver
from modes.utils_ui import clear_console
from modes.utils_ui import validate_login_or_exit
from config import ORDERBOOK_REFRESH_INTERVAL


def _parse_rows(rows):
    prices, amounts = [], []
    for row in rows:
        try:
            price = (
                row.find_element(By.CSS_SELECTOR, ".col-price")
                .text.strip()
                .replace(",", "")
            )
            amount = (
                row.find_element(By.CSS_SELECTOR, ".col-amount")
                .text.strip()
                .replace(",", "")
            )
            if price and amount and price != "-" and amount != "-":
                prices.append(float(price))
                amounts.append(float(amount))
        except Exception:
            continue
    return prices, amounts


def _get_victoria_last_price(driver) -> float:
    price_text = (
        driver.find_element(
            By.CSS_SELECTOR, "div.overturn-cell.col-price span.contrast"
        )
        .text.strip()
        .replace(",", "")
    )
    return float(price_text)


def _fetch_victoria_orderbook_snapshot(driver):
    ask_rows = driver.find_elements(
        By.CSS_SELECTOR, "#mCSB_2_container > a.bidding-table-rows"
    )
    bid_rows = driver.find_elements(
        By.CSS_SELECTOR, "#mCSB_3_container > a.bidding-table-rows"
    )

    ask_prices, ask_amounts = _parse_rows(ask_rows)
    bid_prices, bid_amounts = _parse_rows(bid_rows)

    coin_name = driver.find_element(By.CSS_SELECTOR, "b.pair-title").text.strip()
    ticker_text = driver.find_element(By.CSS_SELECTOR, "span.unit").text.strip()
    coin_ticker = ticker_text.replace("/USDT", "")

    if len(ask_prices) < 10 or len(bid_prices) < 10:
        return None

    asks_sorted = sorted(zip(ask_prices, ask_amounts), reverse=True)
    bids_sorted = sorted(zip(bid_prices, bid_amounts), reverse=True)

    asks = asks_sorted[-10:]
    bids = bids_sorted[:10]

    last_price = _get_victoria_last_price(driver)

    return coin_name, coin_ticker, last_price, asks, bids


def _print_orderbook(coin_name, coin_ticker, last_price, asks, bids):
    print(f"┌───────────── {time.strftime('%H:%M:%S')}  {coin_ticker} ─────────────┐")

    print("")
    for i, (price, amount) in enumerate(asks[:10], 1):
        print(f" {11 - i:2d}Level │ {price:>14,.8f} │ {amount:>14,.8f}")
    print("                 🟦 Asks")

    print(f"\n        💎 Last Price │ {last_price:>14,.8f}")

    print("\n                 🔴 Bids")
    for i, (price, amount) in enumerate(bids, 1):
        print(f" {i:2d}Level │ {price:>14,.8f} │ {amount:>14,.8f}")

    print("\n└" + "─" * 41 + "┘\n")


def run_victoria_orderbook_mode(victoria_url: str):
    driver = init_driver()

    try:
        driver.get(f"{victoria_url}/account/login")

        validate_login_or_exit(driver=driver, mode=1)

        driver.get(f"{victoria_url}/trade")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a.bidding-table-rows"))
        )

        while True:
            try:
                snapshot = _fetch_victoria_orderbook_snapshot(driver)
                if snapshot is None:
                    time.sleep(0.5)
                    continue

                coin_name, coin_ticker, last_price, asks, bids = snapshot
                clear_console()
                _print_orderbook(coin_name, coin_ticker, last_price, asks, bids)

                time.sleep(ORDERBOOK_REFRESH_INTERVAL)

            except KeyboardInterrupt:
                print("\nStopped by user. Returning to menu...")
                return

            except Exception as e:
                print(f"[WARN] Order book error: {type(e).__name__} - {e}")
                time.sleep(1)

    finally:
        driver.quit()
        print("Driver shutdown complete.")
