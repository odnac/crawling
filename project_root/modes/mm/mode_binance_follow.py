# mode_binance_follow.py
from __future__ import annotations

import random
import time
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
    MM_REBASE_INTERVAL_SEC,
    MM_TOPUP_INTERVAL_SEC,
    MM_STEP_PERCENT,
    MM_CANCEL_ROW_TIMEOUT_SEC,
    MM_MAX_CANCEL_OPS_PER_CYCLE,
    MM_BUY_BUDGET_RATIO,
    MM_SELL_QTY_RATIO,
    MM_TOAST_WAIT_SEC,
    FLAG_REMOVE_EXCESS_ORDERS_ENABLE,
    FLAG_ADJUSTMENT_ENABLE,
)
from modes.utils_driver import init_driver
from modes.mm.victoria_account_balance import (
    get_available_buy_usdt,
    get_available_sell_qty,
)
from modes.mm.victoria_trade import place_limit_order
from modes.mm.victoria_orders import read_open_orders_side, cancel_open_orders_row
from modes.market_data import get_binance_price
from modes.utils_logging import setup_logger
from modes.utils_ui import validate_login_or_exit


Side = Literal["bid", "ask"]


@dataclass(frozen=True)
class EngineConfig:
    levels: int
    rebase_interval_sec: int
    topup_interval_sec: int
    step_percent: float
    cancel_row_timeout_sec: int
    max_cancel_ops_per_cycle: int
    buy_budget_ratio: float
    sell_qty_ratio: float
    toast_wait_sec: float
    anchor_order_budget_ratio: float


def _build_cfg() -> EngineConfig:
    return EngineConfig(
        levels=MM_LEVELS,
        rebase_interval_sec=MM_REBASE_INTERVAL_SEC,
        topup_interval_sec=MM_TOPUP_INTERVAL_SEC,
        step_percent=MM_STEP_PERCENT,
        cancel_row_timeout_sec=MM_CANCEL_ROW_TIMEOUT_SEC,
        max_cancel_ops_per_cycle=MM_MAX_CANCEL_OPS_PER_CYCLE,
        buy_budget_ratio=MM_BUY_BUDGET_RATIO,
        sell_qty_ratio=MM_SELL_QTY_RATIO,
        toast_wait_sec=MM_TOAST_WAIT_SEC,
        anchor_order_budget_ratio=0.1,
    )


def _now() -> float:
    return time.time()


def _step_ratio(step_percent: float) -> float:
    return step_percent / 100.0


def _normalize_price(price: float) -> float:
    return round(float(price), 3)


def _normalize_qty(qty: float) -> float:
    return round(float(qty), 8)


def _weights_pyramid(n: int) -> List[float]:
    raw = list(range(1, n + 1))
    s = sum(raw)
    return [r / s for r in raw]


def _sleep_tiny():
    time.sleep(0.15)


def _maybe_wait_toast(cfg: EngineConfig):
    if cfg.toast_wait_sec and cfg.toast_wait_sec > 0:
        time.sleep(cfg.toast_wait_sec)


def _victoria_trade_url(victoria_url: str, ticker: str) -> str:
    return f"{victoria_url}/trade?code=USDT-{ticker.upper()}"


class FollowMMEngine:
    def __init__(self, driver, side: Side, cfg: EngineConfig, ticker: str):
        self.logger = setup_logger(side)
        self.driver = driver
        self.side = side
        self.cfg = cfg
        self.ticker = ticker.upper()

        self._step = _step_ratio(cfg.step_percent)
        self._anchor_price: Optional[float] = None
        self._prev_anchor_price: Optional[float] = None
        self._price_adjustment: Optional[float] = None
        self._last_rebase_ts = 0.0
        self._last_topup_ts = 0.0
        self._rebase_lock = False

    def run_mm(self):
        self.full_rebalance()

        while True:
            now = _now()

            if now - self._last_rebase_ts >= self.cfg.rebase_interval_sec:
                self._sync_with_binance()

            if (not self._rebase_lock) and (
                now - self._last_topup_ts >= self.cfg.topup_interval_sec
            ):
                self._topup_missing_orders()

            time.sleep(0.5)

    def full_rebalance(self):
        self._rebase_lock = True
        try:
            symbol = f"{self.ticker.upper()}USDT"
            self._anchor_price = get_binance_price(symbol)
            # if
            if FLAG_ADJUSTMENT_ENABLE:
                self._price_adjustment = (
                    random.uniform(ADJUSTMENT_MIN, ADJUSTMENT_MAX) / 100.0
                )
            # else
            else:
                self._price_adjustment = self._anchor_price
            # endif

            self.logger.info(
                f"{self.ticker} [FULL REBALANCE] Binance={self._anchor_price:.3f} "
                f"Adjustment={self._price_adjustment*100:.2f}%"
            )

            # if
            if FLAG_REMOVE_EXCESS_ORDERS_ENABLE:
                self._remove_excess_orders()
            # else
            else:
                pass
            # endif

            self._place_anchor_order()
            self._fill_ladder_to_target()
            self._last_rebase_ts = _now()

            self._prev_anchor_price = self._anchor_price

        finally:
            self._rebase_lock = False

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
                    f"(+{price_change:.3f}, +{price_change_percent:.2f}%) → REBALANCE (pull market up)"
                )
                self.full_rebalance()
            elif orderbook_empty or price_change < 0:
                reason = "EMPTY ORDERBOOK" if orderbook_empty else "PRICE DOWN"
                self.logger.info(
                    f"{self.ticker} [{reason}] "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"({price_change:.3f}, {price_change_percent:.2f}%) → FILL ONLY (ASK bot adjusting)"
                )
                self._fill_orderbook_only(new_price)
        else:
            if price_change < 0:
                self.logger.info(
                    f"{self.ticker} [PRICE DOWN] "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"({price_change:.3f}, {price_change_percent:.2f}%) → REBALANCE (pull market down)"
                )
                self.full_rebalance()
            elif orderbook_empty or price_change > 0:
                reason = "EMPTY ORDERBOOK" if orderbook_empty else "PRICE UP"
                self.logger.info(
                    f"{self.ticker} [{reason}] "
                    f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                    f"(+{price_change:.3f}, +{price_change_percent:.2f}%) → FILL ONLY (BID bot adjusting)"
                )
                self._fill_orderbook_only(new_price)

    def _fill_orderbook_only(self, binance_price: float):
        self._anchor_price = binance_price

        if self._price_adjustment is None:
            self._price_adjustment = (
                random.uniform(ADJUSTMENT_MIN, ADJUSTMENT_MAX) / 100.0
            )

        self.logger.info(
            f"{self.ticker} [FILL ORDERS ONLY] Binance={self._anchor_price:.3f} "
            f"Adjustment={self._price_adjustment*100:.2f}%"
        )

        prices = self._calculate_orderbook_levels()
        self._place_orderbook_orders(prices)

        self._last_rebase_ts = _now()

        self._prev_anchor_price = binance_price

    def _topup_missing_orders(self):
        if self._anchor_price is None or self._price_adjustment is None:
            self.full_rebalance()
            return

        self.logger.info(f"{self.ticker} [TOPUP] Filling missing orders")
        self._fill_ladder_to_target()
        self._last_topup_ts = _now()

    def _place_anchor_order(self):
        if self._anchor_price is None or self._price_adjustment is None:
            return

        max_retries = 3
        retry_count = 0

        if self.side == "bid":
            discount = self._price_adjustment
            anchor_price = _normalize_price(self._anchor_price * (1 - discount))

            try:
                usdt = (
                    get_available_buy_usdt(self.driver)
                    * self.cfg.buy_budget_ratio
                    * self.cfg.anchor_order_budget_ratio
                )
                qty = _normalize_qty(usdt / anchor_price)

                if qty > 0:
                    self.logger.info(
                        f"{self.ticker} [ANCHOR ORDER] BID "
                        f"price={anchor_price:.3f} qty={qty:.8f} "
                        f"(Binance: {self._anchor_price:.3f}, Discount: {discount*100:.2f}%)"
                    )

                    while retry_count < max_retries:
                        try:
                            place_limit_order(self.driver, "bid", anchor_price, qty)
                            _maybe_wait_toast(self.cfg)
                            _sleep_tiny()
                            break
                        except Exception as e:
                            retry_count += 1
                            self.logger.warning(
                                f"Anchor order failed (attempt {retry_count}/{max_retries}): {e}"
                            )
                            if retry_count >= max_retries:
                                self.logger.error(
                                    "Anchor order failed after max retries"
                                )
                            else:
                                time.sleep(1)
            except Exception as e:
                self.logger.error(f"Failed to get balance for anchor order: {e}")
        else:
            premium = self._price_adjustment
            anchor_price = _normalize_price(self._anchor_price * (1 + premium))

            try:
                coin = (
                    get_available_sell_qty(self.driver)
                    * self.cfg.sell_qty_ratio
                    * self.cfg.anchor_order_budget_ratio
                )
                qty = _normalize_qty(coin)

                if qty > 0:
                    self.logger.info(
                        f"{self.ticker} [ANCHOR ORDER] ASK "
                        f"price={anchor_price:.3f} qty={qty:.8f} "
                        f"(Binance: {self._anchor_price:.3f}, Premium: {premium*100:.2f}%)"
                    )

                    while retry_count < max_retries:
                        try:
                            place_limit_order(self.driver, "ask", anchor_price, qty)
                            _maybe_wait_toast(self.cfg)
                            _sleep_tiny()
                            break
                        except Exception as e:
                            retry_count += 1
                            self.logger.warning(
                                f"Anchor order failed (attempt {retry_count}/{max_retries}): {e}"
                            )
                            if retry_count >= max_retries:
                                self.logger.error(
                                    "Anchor order failed after max retries"
                                )
                            else:
                                time.sleep(1)
            except Exception as e:
                self.logger.error(f"Failed to get balance for anchor order: {e}")

    def _fill_ladder_to_target(self):
        rows = read_open_orders_side(self.driver, self.side)
        need = self.cfg.levels - len(rows)
        if need <= 0:
            # if
            if FLAG_REMOVE_EXCESS_ORDERS_ENABLE:
                self._remove_excess_orders()
            # else
            else:
                return
            # endif

        if len(rows) == 0:
            prices = self._calculate_orderbook_levels()
            self._place_orderbook_orders(prices)
            return

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

        self._place_orderbook_orders(new_prices)

    def _place_orderbook_orders(self, prices: List[float]):
        if not prices:
            return

        if self.side == "bid":
            try:
                usdt = (
                    get_available_buy_usdt(self.driver)
                    * self.cfg.buy_budget_ratio
                    * (1 - self.cfg.anchor_order_budget_ratio)
                )
            except Exception as e:
                self.logger.error(f"Failed to get USDT balance: {e}")
                return

            weights = _weights_pyramid(len(prices))

            for price, w in zip(prices, weights):
                budget = usdt * w
                qty = _normalize_qty(budget / price)
                if qty <= 0:
                    continue
                self.logger.info(
                    f"{self.ticker} [LADDER] {self.side.upper()} "
                    f"price={price:.3f} qty={qty:.8f}"
                )
                try:
                    place_limit_order(self.driver, "bid", price, qty)
                    _maybe_wait_toast(self.cfg)
                    _sleep_tiny()
                except Exception as e:
                    self.logger.warning(f"Failed to place ladder order at {price}: {e}")
                    continue
        else:
            try:
                coin = (
                    get_available_sell_qty(self.driver)
                    * self.cfg.sell_qty_ratio
                    * (1 - self.cfg.anchor_order_budget_ratio)
                )
            except Exception as e:
                self.logger.error(f"Failed to get coin balance: {e}")
                return

            weights = _weights_pyramid(len(prices))

            for price, w in zip(prices, weights):
                qty = _normalize_qty(coin * w)
                if qty <= 0:
                    continue
                self.logger.info(
                    f"{self.ticker} [LADDER] {self.side.upper()} "
                    f"price={price:.3f} qty={qty:.8f}"
                )
                try:
                    place_limit_order(self.driver, "ask", price, qty)
                    _maybe_wait_toast(self.cfg)
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
                _maybe_wait_toast(self.cfg)
                ops += 1
            except (StaleElementReferenceException, WebDriverException):
                continue

    def _calculate_orderbook_levels(self) -> List[float]:
        assert self._anchor_price is not None
        assert self._price_adjustment is not None

        p_anchor = self._anchor_price
        d = self._price_adjustment
        s = self._step

        prices: List[float] = []

        if self.side == "bid":
            base = p_anchor * (1 - d)
            for k in range(1, self.cfg.levels + 1):
                pk = base * ((1 - s) ** k)
                prices.append(_normalize_price(pk))
        else:
            base = p_anchor * (1 + d)
            for k in range(1, self.cfg.levels + 1):
                pk = base * ((1 + s) ** k)
                prices.append(_normalize_price(pk))

        return prices


def run_follow_mm_bid(victoria_url: str, ticker: str):
    cfg = _build_cfg()
    driver = init_driver()

    try:
        driver.get(f"{victoria_url}/account/login")
        validate_login_or_exit(driver=driver, mode=3)
        driver.get(_victoria_trade_url(victoria_url, ticker))
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


def run_follow_mm_ask(victoria_url: str, ticker: str):
    cfg = _build_cfg()
    driver = init_driver()

    try:
        driver.get(f"{victoria_url}/account/login")
        validate_login_or_exit(driver=driver, mode=4)
        driver.get(_victoria_trade_url(victoria_url, ticker))
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
