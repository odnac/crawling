# vic_account_balance.py
from __future__ import annotations

import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def _parse_number(text: str) -> float:
    if text is None:
        raise ValueError("empty text")
    t = text.strip()
    t = re.sub(r"[^0-9\-,.]", "", t)
    t = t.replace(",", "")
    if t in ("", "-", ".", "-."):
        raise ValueError(f"cannot parse number: {text!r}")
    return float(t)


def get_available_buy_usdt(driver, timeout: int = 10) -> float:
    el = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, "user_base_trans"))
    )
    return _parse_number(el.text)


def get_available_sell_qty(driver, timeout: int = 10) -> float:
    el = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, "user_base_coin"))
    )
    return _parse_number(el.text)
