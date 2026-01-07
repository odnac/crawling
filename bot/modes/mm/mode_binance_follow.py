# mode_binance_follow.py
from __future__ import annotations

import random
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dataclasses import dataclass
from typing import Literal, List, Optional
from selenium.common.exceptions import (
    StaleElementReferenceException,
    WebDriverException,
)
from config import (
    ADJUSTMENT_MIN,
    ADJUSTMENT_MAX,
    MM_LEVELS,
    MM_REBALANCE_INTERVAL_SEC,
    MM_REFILL_INTERVAL_SEC,
    MM_STEP_PERCENT,
    MM_CANCEL_ROW_TIMEOUT_SEC,
    MM_MAX_CANCEL_OPS_PER_CYCLE,
    MM_BUY_BUDGET_RATIO,
    MM_SELL_QTY_RATIO,
    MM_TOAST_WAIT_SEC,
    FLAG_REMOVE_EXCESS_ORDERS_ENABLE,
    FLAG_ADJUSTMENT_ENABLE,
    ANCHOR_ORDER_BUDGET_RATIO,
    MIN_ORDER_USDT,
    MM_DISTRIBUTION_MODE,  # NEW: "EQUAL" or "PYRAMID"
)
from modes.utils_driver import init_driver
from modes.mm.vic_account_balance import (
    get_available_buy_usdt,
    get_available_sell_qty,
)
from modes.mm.vic_trade import place_limit_order
from modes.mm.vic_orders import (
    read_open_orders_side,
    cancel_open_orders_row,
    cancel_all_open_orders,
)
from modes.market_data import get_binance_price
from modes.utils_logging import setup_logger
from modes.utils_ui import validate_login_or_exit


Side = Literal["bid", "ask"]


@dataclass(frozen=True)
class EngineConfig:
    levels: int
    rebalance_interval_sec: int
    refill_interval_sec: int
    step_percent: float
    cancel_row_timeout_sec: int
    max_cancel_ops_per_cycle: int
    buy_budget_ratio: float
    sell_qty_ratio: float
    toast_wait_sec: float
    anchor_order_budget_ratio: float
    min_order_usdt: float
    fixed_amount: Optional[float] = None  # NEW: Fixed USDT amount
    distribution_mode: str = "EQUAL"  # NEW: "EQUAL" or "PYRAMID"


@dataclass
class OrderbookLevel:
    price: float
    qty: float


def _build_cfg(fixed_amount: Optional[float] = None) -> EngineConfig:
    return EngineConfig(
        levels=MM_LEVELS,
        rebalance_interval_sec=MM_REBALANCE_INTERVAL_SEC,
        refill_interval_sec=MM_REFILL_INTERVAL_SEC,
        step_percent=MM_STEP_PERCENT,
        cancel_row_timeout_sec=MM_CANCEL_ROW_TIMEOUT_SEC,
        max_cancel_ops_per_cycle=MM_MAX_CANCEL_OPS_PER_CYCLE,
        buy_budget_ratio=MM_BUY_BUDGET_RATIO,
        sell_qty_ratio=MM_SELL_QTY_RATIO,
        toast_wait_sec=MM_TOAST_WAIT_SEC,
        anchor_order_budget_ratio=ANCHOR_ORDER_BUDGET_RATIO,
        min_order_usdt=MIN_ORDER_USDT,
        fixed_amount=fixed_amount,  # NEW
        distribution_mode=MM_DISTRIBUTION_MODE,  # NEW
    )


# ... (keep all utility functions as they are)
def _now() -> float:
    return time.time()


def _step_ratio(step_percent: float) -> float:
    return step_percent / 100.0


def _normalize_price(price: float) -> float:
    return round(float(price), 8)


def _normalize_qty(qty: float) -> float:
    return round(float(qty), 8)


def _parse_number(text: str) -> float:
    t = (text or "").strip()
    t = re.sub(r"[^0-9\-,.]", "", t).replace(",", "")
    if not t:
        raise ValueError(f"cannot parse number from: {text!r}")
    return float(t)


def _weights_pyramid(n: int) -> List[float]:
    raw = list(range(1, n + 1))
    s = sum(raw)
    return [r / s for r in raw]


def _weights_equal(n: int) -> List[float]:
    """
    Equal distribution: each level gets the same weight
    """
    return [1.0 / n for _ in range(n)]


def _get_weights(n: int, mode: str) -> List[float]:
    """
    Get weight distribution based on mode
    mode: "EQUAL" or "PYRAMID"
    """
    if mode == "EQUAL":
        return _weights_equal(n)
    else:  # "PYRAMID"
        return _weights_pyramid(n)


def _sleep_tiny():
    time.sleep(0.15)


def _vic_trade_url(vic_url: str, ticker: str) -> str:
    return f"{vic_url}/trade?code=USDT-{ticker.upper()}"


def read_orderbook(driver, side: Side, timeout: int = 5) -> List[OrderbookLevel]:
    container_id = "order-box-ask" if side == "ask" else "order-box-bid"
    try:
        container = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, container_id))
        )
        rows = container.find_elements(By.CSS_SELECTOR, "a.bidding-table-rows")

        levels = []
        for row in rows:
            try:
                price_text = row.find_element(By.CSS_SELECTOR, "div.col-price").text
                qty_text = row.find_element(By.CSS_SELECTOR, "div.col-cost").text
                levels.append(
                    OrderbookLevel(
                        price=_parse_number(price_text), qty=_parse_number(qty_text)
                    )
                )
            except Exception:
                continue
        return levels
    except Exception:
        return []


class FollowMMEngine:
    def __init__(self, driver, side: Side, cfg: EngineConfig, ticker: str):
        self.logger = setup_logger(side, ticker)
        self.driver = driver
        self.side = side
        self.cfg = cfg
        self.ticker = ticker.upper()
        self._step = _step_ratio(cfg.step_percent)
        self._anchor_price: Optional[float] = None
        self._prev_anchor_price: Optional[float] = None
        self._price_adjustment: Optional[float] = None
        self._last_rebalance_ts = 0.0
        self._last_refill_ts = 0.0
        self._rebalance_lock = False

        # NEW: Log fixed amount mode and distribution mode
        if cfg.fixed_amount is not None:
            self.logger.info(
                f"{self.ticker} [FIXED AMOUNT MODE] Using {cfg.fixed_amount:.2f} USDT per trade"
            )
        self.logger.info(f"{self.ticker} [DISTRIBUTION MODE] {cfg.distribution_mode}")

    # ... (keep _ensure_clean_start and run_mm as they are)
    def _ensure_clean_start(self):
        self.logger.info(f"{self.ticker} [INIT] Initializing market maker...")
        time.sleep(3)

        max_attempts = 3
        for attempt in range(max_attempts):
            success, total = cancel_all_open_orders(self.driver)
            self.logger.info(
                f"{self.ticker} [Cleanup attempt {attempt+1}]: "
                f"{success}/{total} orders cancelled."
            )

            if total == 0:
                self.logger.info("✅ No orders to cleanup")
                return

            if success == total:
                self.logger.info(f"✅ All {total} orders cleaned up successfully")
                return

            if success > 0:
                self.logger.warning(
                    f"⚠️ Partial cleanup: {success}/{total} orders cancelled"
                )

            if attempt < max_attempts - 1:
                self.logger.warning("Retrying cleanup in 2 seconds...")
                time.sleep(2)

        self.logger.critical(
            f"{self.ticker} ❌ CRITICAL ERROR: Cleanup failed after {max_attempts} attempts. "
            "Some orders may still exist. Program will stop to prevent conflicts."
        )
        raise RuntimeError(
            f"Order cleanup failed for {self.ticker} after {max_attempts} attempts. "
            "Check logs for details. Program stopped."
        )

    def run_mm(self):
        self._ensure_clean_start()

        bid_orders = read_open_orders_side(self.driver, "bid")
        ask_orders = read_open_orders_side(self.driver, "ask")

        if bid_orders or ask_orders:
            self.logger.error(
                f"⚠️ CRITICAL: Orders still exist after cleanup! "
                f"BID: {len(bid_orders)}, ASK: {len(ask_orders)}"
            )
            return

        self.full_rebalance()

        while True:
            now = _now()

            if now - self._last_rebalance_ts >= self.cfg.rebalance_interval_sec:
                self._sync_with_binance()

            if (not self._rebalance_lock) and (
                now - self._last_refill_ts >= self.cfg.refill_interval_sec
            ):
                self._refill_missing_orders()

            time.sleep(0.5)

    # ... (keep full_rebalance, _set_current_price_and_anchor, etc. mostly unchanged)
    def full_rebalance(self):
        self._rebalance_lock = True
        try:
            symbol = f"{self.ticker.upper()}USDT"
            self._anchor_price = get_binance_price(symbol)

            if FLAG_ADJUSTMENT_ENABLE:
                self._price_adjustment = (
                    random.uniform(ADJUSTMENT_MIN, ADJUSTMENT_MAX) / 100.0
                )
            else:
                self._price_adjustment = 0.0

            self.logger.info(
                f"{self.ticker} [FULL REBALANCE] Binance={self._anchor_price:.3f} "
                f"Adjustment={self._price_adjustment*100:.2f}%"
            )

            if FLAG_REMOVE_EXCESS_ORDERS_ENABLE:
                self._remove_excess_orders()

            self._set_current_price_and_anchor()
            self._refill_ladder_to_target()

            self._last_rebalance_ts = _now()
            self._prev_anchor_price = self._anchor_price

        finally:
            self._rebalance_lock = False

    def _set_current_price_and_anchor(self):
        if self._anchor_price is None:
            return

        target_price = _normalize_price(self._anchor_price)
        bait_qty = _normalize_qty(self.cfg.min_order_usdt / target_price)

        is_bid = self.side == "bid"
        opp_side = "ask" if is_bid else "bid"
        my_side = "bid" if is_bid else "ask"

        self.logger.info(
            f"{self.ticker} [BAIT] {opp_side.upper()} {target_price:.3f} qty={bait_qty:.8f}"
        )
        if not self._retry_order(opp_side, target_price, bait_qty, "BAIT"):
            self.logger.critical(
                f"{self.ticker} ❌ CRITICAL ERROR: BAIT order failed after retries. "
                "Program will stop to prevent unexpected behavior."
            )
            raise RuntimeError(
                f"BAIT order failed for {self.ticker}. "
                "Check logs for details. Program stopped."
            )

        time.sleep(0.3)
        blocking_orders = self._get_blocking_orders(opp_side, target_price, is_bid)

        total_sweep_qty = bait_qty + sum(o.qty for o in blocking_orders)
        if not self._check_balance_available(total_sweep_qty, target_price, is_bid):
            return

        self.logger.info(
            f"{self.ticker} [SWEEP] {my_side.upper()} {total_sweep_qty:.8f} units"
        )
        if not self._retry_order(my_side, target_price, total_sweep_qty, "SWEEP"):
            return

        self._place_anchor_order(my_side, target_price, is_bid)
        self.logger.info(f"{self.ticker} ✅ Setup complete at {target_price:.3f}")

    def _retry_order(self, side, price, qty, label, max_retries=3):
        for i in range(max_retries):
            try:
                place_limit_order(self.driver, side, price, qty)
                _sleep_tiny()
                return True
            except Exception as e:
                self.logger.warning(f"{label} failed ({i+1}/{max_retries}): {e}")
                if i < max_retries - 1:
                    time.sleep(1)
        self.logger.error(f"{label} retry failed - SKIP")
        return False

    def _get_blocking_orders(self, side, target_price, is_bid):
        orders = read_orderbook(self.driver, side)
        if is_bid:
            blocking = [o for o in orders if o.price < target_price]
        else:
            blocking = [o for o in orders if o.price > target_price]

        if blocking:
            self.logger.info(
                f"{self.ticker} [SWEEP] Found {len(blocking)} blocking orders"
            )
        return blocking

    def _check_balance_available(self, qty, price, is_bid):
        try:
            if is_bid:
                # NEW: Use fixed amount if configured
                if self.cfg.fixed_amount is not None:
                    avail = self.cfg.fixed_amount
                else:
                    avail = (
                        get_available_buy_usdt(self.driver) * self.cfg.buy_budget_ratio
                    )

                needed = qty * price
                if needed > avail:
                    self.logger.error(
                        f"[INSUFFICIENT USDT] Need: {needed:.2f}, Avail: {avail:.2f}"
                    )
                    return False
            else:
                avail = get_available_sell_qty(self.driver) * self.cfg.sell_qty_ratio
                if qty > avail:
                    self.logger.error(
                        f"[INSUFFICIENT QTY] Need: {qty:.8f}, Avail: {avail:.8f}"
                    )
                    return False
            return True
        except Exception as e:
            self.logger.error(f"Balance check error: {e}")
            return False

    def _place_anchor_order(self, side, price, is_bid):
        try:
            if is_bid:
                # NEW: Use fixed amount if configured
                if self.cfg.fixed_amount is not None:
                    usdt = self.cfg.fixed_amount * self.cfg.anchor_order_budget_ratio
                else:
                    usdt = (
                        get_available_buy_usdt(self.driver)
                        * self.cfg.buy_budget_ratio
                        * self.cfg.anchor_order_budget_ratio
                    )
                qty = _normalize_qty(usdt / price)
            else:
                qty = _normalize_qty(
                    get_available_sell_qty(self.driver)
                    * self.cfg.sell_qty_ratio
                    * self.cfg.anchor_order_budget_ratio
                )

            if qty > 0:
                self.logger.info(
                    f"{self.ticker} [ANCHOR] {side.upper()} {price:.3f} qty={qty:.8f}"
                )
                self._retry_order(side, price, qty, "ANCHOR")
        except Exception as e:
            self.logger.error(f"Anchor failed: {e}")

    # ... (keep _sync_with_binance, _refill_orderbook_only, etc.)
    def _sync_with_binance(self):
        symbol = f"{self.ticker.upper()}USDT"

        try:
            new_price = get_binance_price(symbol)
        except Exception as e:
            self.logger.error(f"Failed to fetch Binance price: {e}")
            return

        if self._prev_anchor_price is None:
            self.full_rebalance()
            return

        price_change = new_price - self._prev_anchor_price
        price_change_percent = (price_change / self._prev_anchor_price) * 100

        try:
            rows = read_open_orders_side(self.driver, self.side)
            orderbook_empty = len(rows) == 0
        except Exception as e:
            self.logger.error(f"Failed to read orderbook: {e}")
            return

        if self.side == "bid":
            if price_change > 0:
                self.logger.info(
                    f"{self.ticker} [PRICE UP]  "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"(+{price_change:.3f}, +{price_change_percent:.2f}%) → REBALANCE"
                )
                self.full_rebalance()
            elif orderbook_empty or price_change < 0:
                reason = "EMPTY ORDERBOOK" if orderbook_empty else "PRICE DOWN"
                self.logger.info(
                    f"{self.ticker} [{reason}] "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"({price_change:.3f}, {price_change_percent:.2f}%) → REFILL ONLY"
                )
                self._refill_orderbook_only(new_price)
        else:
            if price_change < 0:
                self.logger.info(
                    f"{self.ticker} [PRICE DOWN] "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"({price_change:.3f}, {price_change_percent:.2f}%) → REBALANCE"
                )
                self.full_rebalance()
            elif orderbook_empty or price_change > 0:
                reason = "EMPTY ORDERBOOK" if orderbook_empty else "PRICE UP"
                self.logger.info(
                    f"{self.ticker} [{reason}] "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"(+{price_change:.3f}, +{price_change_percent:.2f}%) → REFILL ONLY"
                )
                self._refill_orderbook_only(new_price)

    def _refill_orderbook_only(self, binance_price: float):
        self._anchor_price = binance_price
        self._prev_anchor_price = self._anchor_price

        if self._price_adjustment is None:
            self._price_adjustment = (
                random.uniform(ADJUSTMENT_MIN, ADJUSTMENT_MAX) / 100.0
            )

        self.logger.info(
            f"{self.ticker} [REFILL ORDERS ONLY] Binance={self._anchor_price:.3f}"
        )

        prices = self._calculate_orderbook_levels()
        self._place_orderbook_orders(
            prices, available_budget=None
        )  # Full rebalance uses total budget

        self._last_rebalance_ts = _now()

    def _refill_missing_orders(self):
        if self._anchor_price is None or self._price_adjustment is None:
            self.full_rebalance()
            return

        self.logger.info(f"{self.ticker} [REFILL] Refilling missing orders")
        self._refill_ladder_to_target()
        self._last_refill_ts = _now()

    def _refill_ladder_to_target(self):
        rows = read_open_orders_side(self.driver, self.side)

        if len(rows) > self.cfg.levels:
            if FLAG_REMOVE_EXCESS_ORDERS_ENABLE:
                self._remove_excess_orders()
                return

        if len(rows) == 0:
            # Empty orderbook - place all levels
            prices = self._calculate_orderbook_levels()
            self._place_orderbook_orders(prices, available_budget=None)
            return

        # Calculate how many levels we need to add
        need = self.cfg.levels - len(rows)

        if need <= 0:
            return  # Orderbook is full

        # Calculate prices for missing levels
        if self.side == "ask":
            outer = max(r.price for r in rows)
            new_prices = [
                _normalize_price(outer * ((1 + self._step) ** i))
                for i in range(1, need + 1)
            ]
        else:
            outer = min(r.price for r in rows)
            new_prices = [
                _normalize_price(outer * ((1 - self._step) ** i))
                for i in range(1, need + 1)
            ]

        # NEW: Calculate remaining budget
        remaining_budget = self._calculate_remaining_budget(rows)

        # Place only the missing levels with remaining budget
        self._place_orderbook_orders(new_prices, available_budget=remaining_budget)

    def _calculate_remaining_budget(self, existing_rows) -> Optional[float]:
        """
        Calculate how much budget is left after accounting for existing orders.
        Returns None for percentage mode (unlimited budget from balance).
        """
        if self.side == "bid":
            # Calculate total budget for ladder orders
            if self.cfg.fixed_amount is not None:
                total_budget = self.cfg.fixed_amount * (
                    1 - self.cfg.anchor_order_budget_ratio
                )
            else:
                return None  # Percentage mode - use available balance

            # Calculate USDT already used in existing orders
            used_budget = 0.0
            for row in existing_rows:
                used_budget += row.price * row.qty

            remaining = total_budget - used_budget

            self.logger.info(
                f"{self.ticker} [BUDGET] Total: {total_budget:.2f} USDT, "
                f"Used: {used_budget:.2f} USDT, Remaining: {remaining:.2f} USDT"
            )

            return max(0, remaining)
        else:
            # For ask side, we don't use budget concept (use coin quantity)
            return None

    # MODIFIED: _place_orderbook_orders to support fixed amount and remaining budget
    def _place_orderbook_orders(
        self, prices: List[float], available_budget: Optional[float] = None
    ):
        if not prices:
            return

        if self.side == "bid":
            try:
                # NEW: Use available_budget if provided (for refill), otherwise calculate total budget
                if available_budget is not None:
                    usdt = available_budget
                elif self.cfg.fixed_amount is not None:
                    usdt = self.cfg.fixed_amount * (
                        1 - self.cfg.anchor_order_budget_ratio
                    )
                else:
                    usdt = (
                        get_available_buy_usdt(self.driver)
                        * self.cfg.buy_budget_ratio
                        * (1 - self.cfg.anchor_order_budget_ratio)
                    )
            except Exception as e:
                self.logger.error(f"Failed to get USDT balance: {e}")
                return

            # NEW: Use distribution mode from config
            weights = _get_weights(len(prices), self.cfg.distribution_mode)

            for price, w in zip(prices, weights):
                budget = usdt * w
                qty = _normalize_qty(budget / price)
                if qty <= 0:
                    continue
                usdt_value = price * qty
                self.logger.info(
                    f"{self.ticker} [LADDER] {self.side.upper()} "
                    f"price={price:.3f} qty={qty:.8f} ≈{usdt_value:,.0f}usdt ({self.cfg.distribution_mode})"
                )
                try:
                    place_limit_order(self.driver, "bid", price, qty)
                    _sleep_tiny()
                except Exception as e:
                    self.logger.warning(f"Failed to place ladder order at {price}: {e}")
                    continue
        else:
            try:
                # For ask side - calculate remaining coin quantity
                if available_budget is not None:
                    # This is a refill - need to calculate remaining coin from existing orders
                    rows = read_open_orders_side(self.driver, "ask")
                    total_coin = (
                        get_available_sell_qty(self.driver)
                        * self.cfg.sell_qty_ratio
                        * (1 - self.cfg.anchor_order_budget_ratio)
                    )
                    used_coin = sum(r.qty for r in rows)
                    coin = max(0, total_coin - used_coin)

                    self.logger.info(
                        f"{self.ticker} [COIN BUDGET] Total: {total_coin:.8f}, "
                        f"Used: {used_coin:.8f}, Remaining: {coin:.8f}"
                    )
                else:
                    coin = (
                        get_available_sell_qty(self.driver)
                        * self.cfg.sell_qty_ratio
                        * (1 - self.cfg.anchor_order_budget_ratio)
                    )
            except Exception as e:
                self.logger.error(f"Failed to get coin balance: {e}")
                return

            # NEW: Use distribution mode from config
            weights = _get_weights(len(prices), self.cfg.distribution_mode)

            for price, w in zip(prices, weights):
                qty = _normalize_qty(coin * w)
                if qty <= 0:
                    continue
                usdt_value = price * qty
                self.logger.info(
                    f"{self.ticker} [LADDER] {self.side.upper()} "
                    f"price={price:.3f} qty={qty:.8f} ≈{usdt_value:,.0f}usdt ({self.cfg.distribution_mode})"
                )
                try:
                    place_limit_order(self.driver, "ask", price, qty)
                    _sleep_tiny()
                except Exception as e:
                    self.logger.warning(f"Failed to place ladder order at {price}: {e}")
                    continue

    def _remove_excess_orders(self):
        rows = read_open_orders_side(self.driver, self.side)

        if len(rows) <= self.cfg.levels:
            return

        if self.side == "ask":
            rows_sorted = sorted(rows, key=lambda r: r.price)
            cancel = rows_sorted[self.cfg.levels :]
            cancel = sorted(cancel, key=lambda r: r.price, reverse=True)
        else:
            rows_sorted = sorted(rows, key=lambda r: r.price, reverse=True)
            cancel = rows_sorted[self.cfg.levels :]
            cancel = sorted(cancel, key=lambda r: r.price)

        ops = 0
        for row in cancel:
            if ops >= self.cfg.max_cancel_ops_per_cycle:
                break
            try:
                cancel_open_orders_row(
                    self.driver, row, timeout=self.cfg.cancel_row_timeout_sec
                )
                ops += 1
            except (StaleElementReferenceException, WebDriverException):
                continue

    def _calculate_orderbook_levels(self) -> List[float]:
        assert self._anchor_price is not None

        p_anchor = self._anchor_price
        s = self._step

        prices: List[float] = []

        if self.side == "bid":
            for k in range(1, self.cfg.levels + 1):
                pk = p_anchor * ((1 - s) ** k)
                prices.append(_normalize_price(pk))
        else:
            for k in range(1, self.cfg.levels + 1):
                pk = p_anchor * ((1 + s) ** k)
                prices.append(_normalize_price(pk))

        return prices


# MODIFIED: Update function signatures to accept fixed_amount
def run_follow_mm_bid(vic_url: str, ticker: str, fixed_amount: Optional[float] = None):
    cfg = _build_cfg(fixed_amount=fixed_amount)
    driver = init_driver()

    try:
        driver.get(f"{vic_url}/account/login")
        validate_login_or_exit(driver=driver, mode=3)
        driver.get(_vic_trade_url(vic_url, ticker))
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "user_base_trans"))
        )
        FollowMMEngine(driver=driver, side="bid", cfg=cfg, ticker=ticker).run_mm()

    except KeyboardInterrupt:
        print("\n[INFO] Follow MM BID stopped by user (KeyboardInterrupt)")
    except Exception as e:
        print(f"\n[ERROR] Follow MM BID crashed: {type(e).__name__} - {e}")
        import traceback

        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except (WebDriverException, Exception) as e:
            print(f"[WARNING] Error during driver cleanup: {e}")
        print("[INFO] Driver shutdown complete.")


def run_follow_mm_ask(vic_url: str, ticker: str, fixed_amount: Optional[float] = None):
    cfg = _build_cfg(fixed_amount=fixed_amount)
    driver = init_driver()

    try:
        driver.get(f"{vic_url}/account/login")
        validate_login_or_exit(driver=driver, mode=4)
        driver.get(_vic_trade_url(vic_url, ticker))
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "user_base_coin"))
        )
        FollowMMEngine(driver=driver, side="ask", cfg=cfg, ticker=ticker).run_mm()

    except KeyboardInterrupt:
        print("\n[INFO] Follow MM ASK stopped by user (KeyboardInterrupt)")
    except Exception as e:
        print(f"\n[ERROR] Follow MM ASK crashed: {type(e).__name__} - {e}")
        import traceback

        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except (WebDriverException, Exception) as e:
            print(f"[WARNING] Error during driver cleanup: {e}")
        print("[INFO] Driver shutdown complete.")
