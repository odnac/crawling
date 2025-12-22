# utils.py
import os
import platform
from modes.security import check_login_success
from config import ENABLE_FLAG


def clear_console():
    # if
    if ENABLE_FLAG:
        os.system("cls" if platform.system() == "Windows" else "clear")
    # else
    else:
        pass
    # endif


def wait_for_manual_login(mode: int):
    mode_titles = {
        1: "Show VictoriaEX Order Book",
        2: "Print Binance-Referenced Price Mode Started",
        3: "Follow MM (BID)",
        4: "Follow MM (ASK)",
    }

    print("\n" + "=" * 45)
    print("         💎 Connected to VictoriaEX 💎")
    print("  Press Enter after logging in to continue.")
    print("=" * 45 + "\n")
    input()

    title = mode_titles.get(mode, "Unknown Mode")
    print(f"\n[Mode {mode}] {title}\n\n")


def validate_login_or_exit(driver, mode: int) -> bool:
    wait_for_manual_login(mode)

    if not check_login_success(driver):
        print("❌ Login failed. Please check your credentials.")
        driver.quit()
        return False

    return True
