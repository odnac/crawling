# referenced_mm_mode.py
import time
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from modes.utils_driver import init_driver
from modes.utils_ui import clear_console
from modes.utils_ui import validate_login_or_exit
from modes.market_data import get_binance_price
from config import (
    ADJUSTMENT_MIN,
    ADJUSTMENT_MAX,
    FOLLOW_UPDATE_SEC,
    FLAG_ADJUSTMENT_ENABLE,
)


def _get_current_binance_symbol_from_vic(driver) -> str:
    unit_text = driver.find_element(By.CSS_SELECTOR, "span.unit").text.strip()
    return unit_text.replace("/", "").upper()


def print_binance_referenced_price_mode(VIC_URL: str):
    driver = init_driver()

    try:
        driver.get(f"{VIC_URL}/account/login")

        validate_login_or_exit(driver=driver, mode=2)

        driver.get(f"{VIC_URL}/trade")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "b.pair-title"))
        )

        while True:
            try:
                symbol = _get_current_binance_symbol_from_vic(driver)

                try:
                    binance_price = get_binance_price(symbol)
                except Exception as e:
                    print(f"[WARN] Binance API error: {type(e).__name__} - {e}")
                    time.sleep(1)
                    continue

                # if
                if FLAG_ADJUSTMENT_ENABLE:
                    adjustment = random.uniform(ADJUSTMENT_MIN, ADJUSTMENT_MAX)
                    target_price = binance_price * (1 - adjustment)

                    clear_console()
                    print(
                        f"[{time.strftime('%H:%M:%S')}] Binance {symbol}={binance_price:.2f}"
                        f"target(-{adjustment*100:.3f}%)={target_price:.2f}"
                    )
                # else
                else:
                    clear_console()
                    print(
                        f"[{time.strftime('%H:%M:%S')}] Binance {symbol}={binance_price:.2f}"
                    )
                # endif

                time.sleep(FOLLOW_UPDATE_SEC)

            except KeyboardInterrupt:
                print("\nStopped by user. Returning to menu...")
                return

    finally:
        driver.quit()
        print("Driver shutdown complete.")
