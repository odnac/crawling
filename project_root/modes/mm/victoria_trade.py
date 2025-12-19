# victoria_trade.py
from __future__ import annotations

import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def _set_input_value(driver, by, locator, value: str, timeout: int = 10):
    el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, locator)))
    el.click()
    el.send_keys(Keys.CONTROL, "a")
    el.send_keys(Keys.DELETE)
    el.send_keys(value)
    return el


def place_limit_order(
    driver, side: str, price: float, qty: float, timeout: int = 10
) -> bool:
    if qty <= 0 or price <= 0:
        return False

    price_str = f"{price:.6f}".rstrip("0").rstrip(".")
    qty_str = f"{qty:.8f}".rstrip("0").rstrip(".")

    if side == "bid":
        _set_input_value(driver, By.ID, "bid_price", price_str, timeout=timeout)
        _set_input_value(driver, By.ID, "bid_coin", qty_str, timeout=timeout)

        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, "btnBuying"))
        )
        btn.click()
    elif side == "ask":
        _set_input_value(driver, By.ID, "ask_price", price_str, timeout=timeout)
        _set_input_value(driver, By.ID, "ask_coin", qty_str, timeout=timeout)

        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, "btnSelling"))
        )
        btn.click()
    else:
        raise ValueError(f"unknown side: {side}")

    time.sleep(0.15)
    return True
