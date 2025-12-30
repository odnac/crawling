from __future__ import annotations

import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    StaleElementReferenceException,
)

FLAG_VIC_TRADE_DEBUGGING_PRINT = False


def _set_input_value(driver, by, locator, value: str, timeout: int = 10):
    wait = WebDriverWait(driver, timeout)
    wait.until(EC.invisibility_of_element_located((By.CLASS_NAME, "swal-overlay")))

    el = wait.until(EC.element_to_be_clickable((by, locator)))
    try:
        el.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", el)
        driver.execute_script("arguments[0].focus();", el)

    el.send_keys(Keys.CONTROL, "a")
    el.send_keys(Keys.DELETE)
    el.send_keys(value)
    return el


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

        if FLAG_VIC_TRADE_DEBUGGING_PRINT:
            print(
                f"[TRADE] {popup_description}: Waiting {wait_animation}s for animation..."
            )
        time.sleep(wait_animation)

        popup_text = _get_popup_text(driver)
        if FLAG_VIC_TRADE_DEBUGGING_PRINT:
            print(f"[TRADE] {popup_description}: Popup text = '{popup_text}'")

        wait.until(EC.element_to_be_clickable(ok_btn))

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                ok_btn.click()
                if FLAG_VIC_TRADE_DEBUGGING_PRINT:
                    print(
                        f"[TRADE] {popup_description}: Click succeeded (attempt {attempt + 1})."
                    )
                break
            except ElementClickInterceptedException:
                if attempt < max_attempts - 1:
                    print(
                        f"[TRADE WARN] {popup_description}: Click intercepted, retrying..."
                    )
                    time.sleep(0.5)
                else:
                    driver.execute_script("arguments[0].click();", ok_btn)
                    if FLAG_VIC_TRADE_DEBUGGING_PRINT:
                        print(f"[TRADE] {popup_description}: JavaScript click used.")

        time.sleep(0.5)
        if FLAG_VIC_TRADE_DEBUGGING_PRINT:
            print(f"[TRADE] {popup_description}: Button clicked successfully.")
        return True

    except TimeoutException as e:
        print(f"[TRADE ERROR] {popup_description}: Timeout - {e}")
        return False
    except Exception as e:
        print(f"[TRADE ERROR] {popup_description}: Unexpected error - {e}")
        return False


def _wait_for_popup_to_appear(driver, max_wait: int = 30, check_interval: float = 0.3):

    if FLAG_VIC_TRADE_DEBUGGING_PRINT:
        print(f"[TRADE] Waiting for popup to appear (max {max_wait}s)...")

    end_time = time.time() + max_wait

    while time.time() < end_time:
        try:
            overlays = driver.find_elements(By.CLASS_NAME, "swal-overlay")

            for overlay in overlays:
                try:
                    if overlay.is_displayed():
                        buttons = overlay.find_elements(
                            By.CSS_SELECTOR,
                            "button.swal-button--ok, button.swal-button--confirm",
                        )
                        if buttons and buttons[0].is_displayed():
                            if FLAG_VIC_TRADE_DEBUGGING_PRINT:
                                print("[TRADE] Popup with OK button detected!")
                            return True
                except (StaleElementReferenceException, Exception):
                    continue

            time.sleep(check_interval)

        except Exception as e:
            print(f"[TRADE WARN] Error while waiting for popup: {e}")
            time.sleep(check_interval)

    print(f"[TRADE WARN] No popup appeared within {max_wait} seconds.")
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


def place_limit_order(
    driver, side: str, price: float, qty: float, timeout: int = 15
) -> bool:
    if qty <= 0 or price <= 0:
        return False

    price_str = f"{price:.6f}".rstrip("0").rstrip(".")
    qty_str = f"{qty:.8f}".rstrip("0").rstrip(".")

    try:
        if side == "bid":
            _set_input_value(driver, By.ID, "bid_price", price_str, timeout=timeout)
            _set_input_value(driver, By.ID, "bid_coin", qty_str, timeout=timeout)
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.ID, "btnBuying"))
            )
            btn.click()
            order_type = "BUY"
        elif side == "ask":
            _set_input_value(driver, By.ID, "ask_price", price_str, timeout=timeout)
            _set_input_value(driver, By.ID, "ask_coin", qty_str, timeout=timeout)
            btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.ID, "btnSelling"))
            )
            btn.click()
            order_type = "SELL"
        else:
            print(f"[TRADE ERROR] Invalid side: {side}")
            return False
        if FLAG_VIC_TRADE_DEBUGGING_PRINT:
            print(f"[TRADE] {order_type} button clicked, waiting for first popup...")

        if not _wait_for_popup_to_appear(driver, max_wait=10, check_interval=0.3):
            print("[TRADE ERROR] First popup did not appear.")
            return False

        if not _click_ok_button(
            driver,
            timeout=timeout,
            popup_description="First confirmation",
            wait_animation=1.2,
        ):
            print("[TRADE ERROR] Failed to click first popup.")
            return False

        if FLAG_VIC_TRADE_DEBUGGING_PRINT:
            print("[TRADE] First popup clicked. Checking for second popup...")

        time.sleep(0.8)

        if _is_popup_visible(driver):
            if FLAG_VIC_TRADE_DEBUGGING_PRINT:
                print("[TRADE] Second popup already visible!")
        else:
            if not _wait_for_popup_to_appear(driver, max_wait=30, check_interval=0.3):
                print(
                    "[TRADE WARN] Second popup did not appear. Order may have failed."
                )
                return False

        if not _click_ok_button(
            driver,
            timeout=30,
            popup_description="Success notification",
            wait_animation=1.5,
        ):
            print("[TRADE ERROR] Failed to click second popup.")
            return False

        try:
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "swal-overlay"))
            )
        except:
            pass

        if FLAG_VIC_TRADE_DEBUGGING_PRINT:
            print(f"[TRADE] {order_type} order completed successfully.")
        return True

    except Exception as e:
        print(f"[TRADE ERROR] place_limit_order failed: {e}")
        return False
