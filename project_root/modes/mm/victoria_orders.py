# victoria_orders.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

Side = Literal["bid", "ask"]

TBODY = (By.CSS_SELECTOR, "tbody#out-standing-list")
ROWS = (By.CSS_SELECTOR, "tbody#out-standing-list > tr")
CANCEL_BTN_IN_ROW = (By.CSS_SELECTOR, "button.order-cancel[data-orderid]")

# row 내부 컬럼 인덱스 (스샷 기준)
# 0: Date, 1: Pair, 2: Type, 3: Price, 4: Qty, 5: Pending Qty, 6: Cancel(btn)
TYPE_TD_IDX = 2
PRICE_TD_IDX = 3


@dataclass
class OrderRow:
    side: Side
    price: float
    order_id: str
    row_el: object


def _parse_number(text: str) -> float:
    t = (text or "").strip()
    t = re.sub(r"[^0-9\-,.]", "", t).replace(",", "")
    if not t:
        raise ValueError(f"cannot parse number from: {text!r}")
    return float(t)


def _infer_side_from_type_text(type_text: str) -> Side:
    t = (type_text or "").strip().lower()
    if t == "buy":
        return "bid"
    if t == "sell":
        return "ask"

    raise ValueError(f"unknown type text: {type_text!r}")


def read_open_orders_side(driver, side: Side, timeout: int = 10) -> List[OrderRow]:
    WebDriverWait(driver, timeout).until(EC.presence_of_element_located(TBODY))
    rows = driver.find_elements(*ROWS)

    out: List[OrderRow] = []
    for tr in rows:
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) <= PRICE_TD_IDX:
            continue

        type_text = tds[TYPE_TD_IDX].text  # 'sell' / 'buy'
        try:
            row_side = _infer_side_from_type_text(type_text)
        except Exception:

            try:
                btn = tr.find_element(*CANCEL_BTN_IN_ROW)
                tradetype = (btn.get_attribute("data-tradetype") or "").strip().lower()
                if tradetype == "ask":
                    row_side = "ask"
                elif tradetype == "bid":
                    row_side = "bid"
                else:
                    continue
            except Exception:
                continue

        if row_side != side:
            continue

        price_text = tds[PRICE_TD_IDX].text  # "90,935\nUSDT" 형태 가능
        try:
            price = _parse_number(price_text)
        except Exception:
            continue

        try:
            btn = tr.find_element(*CANCEL_BTN_IN_ROW)
            order_id = btn.get_attribute("data-orderid")
            if not order_id:
                continue
        except Exception:
            continue

        out.append(OrderRow(side=row_side, price=price, order_id=order_id, row_el=tr))

    return out


def cancel_open_orders_row(driver, order_row: OrderRow, timeout: int = 10) -> bool:
    order_id = order_row.order_id

    try:
        btn = order_row.row_el.find_element(*CANCEL_BTN_IN_ROW)
        btn.click()
    except Exception:
        pass

    selector = f'button.order-cancel[data-orderid="{order_id}"]'
    try:
        WebDriverWait(driver, timeout).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, selector))
        )
        return True
    except Exception:
        return False
