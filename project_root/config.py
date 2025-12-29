# config.py
import os
from dotenv import load_dotenv


load_dotenv()

CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH")
VICTORIA_URL = os.getenv("VICTORIA_URL")


def get_env_float(key: str) -> float:
    value = os.getenv(key)
    if value is None:
        raise RuntimeError(f"[ENV ERROR] {key} is not set in .env")
    return float(value)


def get_env_int(key: str) -> int:
    value = os.getenv(key)
    if value is None:
        raise RuntimeError(f"[ENV ERROR] {key} is not set in .env")
    return int(value)


FLAG_LOGIN_ENABLE = False
FLAG_ADJUSTMENT_ENABLE = False
FLAG_CLEAR_CONSOLE_ENABLE = False
FLAG_REMOVE_EXCESS_ORDERS_ENABLE = False

ORDERBOOK_REFRESH_INTERVAL = get_env_float("ORDERBOOK_REFRESH_INTERVAL")

ADJUSTMENT_MIN = get_env_float("ADJUSTMENT_MIN")
ADJUSTMENT_MAX = get_env_float("ADJUSTMENT_MAX")
FOLLOW_UPDATE_SEC = get_env_int("FOLLOW_UPDATE_SEC")

MM_LEVELS = get_env_int("MM_LEVELS")
MM_REBASE_INTERVAL_SEC = get_env_int("MM_REBASE_INTERVAL_SEC")
MM_TOPUP_INTERVAL_SEC = get_env_int("MM_TOPUP_INTERVAL_SEC")
MM_STEP_PERCENT = get_env_float("MM_STEP_PERCENT")
MM_CANCEL_ROW_TIMEOUT_SEC = get_env_int("MM_CANCEL_ROW_TIMEOUT_SEC")
MM_MAX_CANCEL_OPS_PER_CYCLE = get_env_int("MM_MAX_CANCEL_OPS_PER_CYCLE")
MM_BUY_BUDGET_RATIO = get_env_float("MM_BUY_BUDGET_RATIO")
MM_SELL_QTY_RATIO = get_env_float("MM_SELL_QTY_RATIO")
MM_TOAST_WAIT_SEC = get_env_float("MM_TOAST_WAIT_SEC")

ANCHOR_ORDER_BUDGET_RATIO = get_env_float("ANCHOR_ORDER_BUDGET_RATIO")

MIN_ORDER_USDT = get_env_float("MIN_ORDER_USDT")
