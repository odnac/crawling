# vic_orders.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import List, Literal
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    StaleElementReferenceException,
)
from config import FLAG_VIC_ORDERS_DEBUGGING_PRINT

Side = Literal["bid", "ask"]

TBODY = (By.CSS_SELECTOR, "tbody#out-standing-list")
ROWS = (By.CSS_SELECTOR, "tbody#out-standing-list > tr")
CANCEL_BTN_IN_ROW = (By.CSS_SELECTOR, "button.order-cancel[data-orderid]")

# row
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


def _get_popup_text(driver):
    try:
        modal = driver.find_element(By.CLASS_NAME, "swal-modal")
        text_el = modal.find_element(By.CLASS_NAME, "swal-text")
        return text_el.text.strip()
    except:
        return ""


def _click_ok_button(
    driver,
    timeout: int = 20,
    popup_description: str = "popup",
    wait_animation: float = 1.2,
):
    wait = WebDriverWait(driver, timeout)

    try:
        wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "swal-overlay")))

        ok_btn = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button.swal-button--ok, button.swal-button--confirm")
            )
        )

        time.sleep(wait_animation)

        popup_text = _get_popup_text(driver)
        if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
            print(f"[CANCEL] {popup_description}: Popup text = '{popup_text}'")

        wait.until(EC.element_to_be_clickable(ok_btn))

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                ok_btn.click()
                if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                    print(
                        f"[CANCEL] {popup_description}: Click succeeded (attempt {attempt + 1})."
                    )
                break
            except ElementClickInterceptedException:
                if attempt < max_attempts - 1:
                    print(
                        f"[CANCEL WARN] {popup_description}: Click intercepted, retrying..."
                    )
                    time.sleep(0.5)
                else:
                    driver.execute_script("arguments[0].click();", ok_btn)
                    if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                        print(f"[CANCEL] {popup_description}: JavaScript click used.")

        time.sleep(0.5)

        if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
            print(f"[CANCEL] {popup_description}: Button clicked successfully.")
        return True

    except TimeoutException as e:
        print(f"[CANCEL ERROR] {popup_description}: Timeout - {e}")
        return False
    except Exception as e:
        print(f"[CANCEL ERROR] {popup_description}: Unexpected error - {e}")
        return False


def _is_popup_visible(driver):
    try:
        overlays = driver.find_elements(By.CLASS_NAME, "swal-overlay")
        for overlay in overlays:
            if overlay.is_displayed():
                buttons = overlay.find_elements(
                    By.CSS_SELECTOR,
                    "button.swal-button--ok, button.swal-button--confirm",
                )
                if buttons and buttons[0].is_displayed():
                    return True
        return False
    except:
        return False


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

        price_text = tds[PRICE_TD_IDX].text  # "90,935\nUSDT"
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


def cancel_open_orders_row(driver, order_row: OrderRow, timeout: int = 15) -> bool:
    order_id = order_row.order_id

    try:
        btn = order_row.row_el.find_element(*CANCEL_BTN_IN_ROW)

        try:
            btn.click()
            if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                print(
                    f"[CANCEL] Button clicked for order {order_id} @ {order_row.price:.3f}"
                )
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", btn)
            if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                print(f"[CANCEL] Button clicked (JS) for order {order_id}")

        time.sleep(0.5)

        if not _click_ok_button(
            driver,
            timeout=timeout,
            popup_description="Cancel confirmation",
            wait_animation=1.2,
        ):
            print(f"[CANCEL ERROR] Failed to confirm cancellation for order {order_id}")
            return False
        if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
            print(
                f"[CANCEL] Confirmation done for order {order_id}. Waiting for success notification..."
            )

        time.sleep(0.8)

        if _is_popup_visible(driver):
            if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                print("[CANCEL] Success notification already visible!")
        else:
            end_time = time.time() + 10
            popup_appeared = False

            while time.time() < end_time:
                if _is_popup_visible(driver):
                    popup_appeared = True
                    break
                time.sleep(0.3)

            if not popup_appeared:
                print(
                    f"[CANCEL WARN] Success notification did not appear for order {order_id}"
                )
                selector = f'button.order-cancel[data-orderid="{order_id}"]'
                try:
                    driver.find_element(By.CSS_SELECTOR, selector)
                    return False
                except:
                    if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                        print(
                            f"[CANCEL] Order {order_id} button disappeared, assuming success"
                        )
                    return True

        time.sleep(0.8)

        if not _click_ok_button(
            driver,
            timeout=timeout,
            popup_description="Cancelled notification",
            wait_animation=1.5,
        ):
            print(
                f"[CANCEL ERROR] Failed to close success notification for order {order_id}"
            )
            return True

        try:
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "swal-overlay"))
            )
        except:
            pass

        if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
            print(
                f"[CANCEL] Order {order_id} @ {order_row.price:.3f} cancelled successfully."
            )
        return True

    except StaleElementReferenceException:
        print(
            f"[CANCEL WARN] Order row became stale for order {order_id} (may have been cancelled already)"
        )
        return False
    except Exception as e:
        print(f"[CANCEL ERROR] Failed to cancel order {order_id}: {e}")
        return False


def _cancel_all_open_orders_side(
    driver, side: Side, timeout: int = 15
) -> tuple[int, int]:

    if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
        print(f"[CANCEL] Starting {side.upper()} orders cancellation...")

    cancelled = 0
    max_iterations = 50

    for iteration in range(max_iterations):
        try:
            rows = read_open_orders_side(driver, side, timeout=10)

            if not rows:
                if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                    if cancelled > 0:
                        print(
                            f"[CANCEL] ✅ All {cancelled} {side.upper()} orders cancelled"
                        )
                    else:
                        print(f"[CANCEL] No {side.upper()} orders to cancel")
                return (cancelled, cancelled)

            first_row = rows[0]

            if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
                print(
                    f"[CANCEL] Cancelling {side.upper()} order {cancelled+1} "
                    f"@ {first_row.price:.3f} (ID: {first_row.order_id})"
                )

            if cancel_open_orders_row(driver, first_row, timeout=timeout):
                cancelled += 1
                time.sleep(0.3)
            else:
                time.sleep(0.5)

        except Exception as e:
            print(f"[CANCEL ERROR] Error during iteration {iteration}: {e}")
            time.sleep(0.5)
            continue

    remaining_rows = read_open_orders_side(driver, side, timeout=5)
    total = cancelled + len(remaining_rows)

    if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
        print(
            f"[CANCEL] ⚠️ Max iterations reached. "
            f"Cancelled {cancelled}/{total} {side.upper()} orders"
        )

    return (cancelled, total)


def cancel_all_open_orders(driver, timeout: int = 15) -> tuple[int, int]:
    if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
        print(f"[CANCEL] Cancelling all orders (both BID and ASK)...")

    bid_success, bid_total = _cancel_all_open_orders_side(driver, "bid", timeout)
    ask_success, ask_total = _cancel_all_open_orders_side(driver, "ask", timeout)

    total_success = bid_success + ask_success
    total_count = bid_total + ask_total

    if FLAG_VIC_ORDERS_DEBUGGING_PRINT:
        print(
            f"[CANCEL] Total: {total_success}/{total_count} orders cancelled (BID: {bid_success}/{bid_total}, ASK: {ask_success}/{ask_total})"
        )

    return (total_success, total_count)
