# mode_binance_dual.py
"""
Dual-side Market Maker: BID + ASK in one engine with single driver
"""
from __future__ import annotations

import random
import time
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dataclasses import dataclass
from typing import List, Optional, Dict
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
    MM_DISTRIBUTION_MODE,
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


@dataclass(frozen=True)
class DualEngineConfig:
    """Configuration for dual-side market maker"""

    levels: int
    rebalance_interval_sec: int
    refill_interval_sec: int
    step_percent: float
    cancel_row_timeout_sec: int
    max_cancel_ops_per_cycle: int
    toast_wait_sec: float
    anchor_order_budget_ratio: float
    min_order_usdt: float
    distribution_mode: str

    # Dual-side specific
    bid_fixed_amount: float
    ask_fixed_amount: float  # USDT value worth of coins


@dataclass
class OrderbookLevel:
    price: float
    qty: float


def _build_dual_cfg(bid_amount: float, ask_amount: float) -> DualEngineConfig:
    """Build configuration for dual-side engine"""
    return DualEngineConfig(
        levels=MM_LEVELS,
        rebalance_interval_sec=MM_REBALANCE_INTERVAL_SEC,
        refill_interval_sec=MM_REFILL_INTERVAL_SEC,
        step_percent=MM_STEP_PERCENT,
        cancel_row_timeout_sec=MM_CANCEL_ROW_TIMEOUT_SEC,
        max_cancel_ops_per_cycle=MM_MAX_CANCEL_OPS_PER_CYCLE,
        toast_wait_sec=MM_TOAST_WAIT_SEC,
        anchor_order_budget_ratio=ANCHOR_ORDER_BUDGET_RATIO,
        min_order_usdt=MIN_ORDER_USDT,
        distribution_mode=MM_DISTRIBUTION_MODE,
        bid_fixed_amount=bid_amount,
        ask_fixed_amount=ask_amount,
    )


# Utility functions
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


def _get_weights(n: int, mode: str) -> List[float]:
    """Get weight distribution based on mode"""
    if mode == "EQUAL":
        return [1.0 / n for _ in range(n)]
    else:  # "PYRAMID"
        raw = list(range(1, n + 1))
        s = sum(raw)
        return [r / s for r in raw]


def _sleep_tiny():
    time.sleep(0.15)


def _vic_trade_url(vic_url: str, ticker: str) -> str:
    return f"{vic_url}/trade?code=USDT-{ticker.upper()}"


def read_orderbook(driver, side: str, timeout: int = 5) -> List[OrderbookLevel]:
    """Read orderbook from the page"""
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


class DualSideMMEngine:
    """Market maker engine that manages both BID and ASK sides with single driver"""

    def __init__(self, driver, cfg: DualEngineConfig, ticker: str):
        self.logger = setup_logger("dual", ticker)
        self.driver = driver
        self.cfg = cfg
        self.ticker = ticker.upper()
        self._step = _step_ratio(cfg.step_percent)

        # Price tracking
        self._anchor_price: Optional[float] = None
        self._prev_anchor_price: Optional[float] = None
        self._price_adjustment: Optional[float] = None

        # Timing
        self._last_rebalance_ts = 0.0
        self._last_refill_ts = 0.0
        self._rebalance_lock = False

        # Log configuration
        self.logger.info(f"{self.ticker} [DUAL MODE INIT]")
        self.logger.info(f"  BID Budget: {cfg.bid_fixed_amount:.2f} USDT")
        self.logger.info(f"  ASK Budget: {cfg.ask_fixed_amount:.2f} USDT (coin value)")
        self.logger.info(f"  Distribution: {cfg.distribution_mode}")
        self.logger.info(f"  Levels: {cfg.levels}")

    def _validate_initial_balance(self):
        """Validate sufficient balance for both sides"""
        try:
            # Check USDT balance for BID side
            available_usdt = get_available_buy_usdt(self.driver)
            required_usdt = self.cfg.bid_fixed_amount * 1.1  # 10% buffer

            self.logger.info(
                f"{self.ticker} [BALANCE CHECK - BID] "
                f"Available: {available_usdt:.2f} USDT, Required: {required_usdt:.2f} USDT"
            )

            if available_usdt < required_usdt:
                raise RuntimeError(
                    f"Insufficient USDT for BID side. "
                    f"Need {required_usdt:.2f} USDT but only have {available_usdt:.2f} USDT"
                )

            # Check coin balance for ASK side
            available_coin = get_available_sell_qty(self.driver)

            if available_coin <= 0:
                raise RuntimeError(f"No {self.ticker} coins available for ASK side")

            # Calculate coin value
            symbol = f"{self.ticker}USDT"
            current_price = get_binance_price(symbol)
            coin_value_usdt = available_coin * current_price
            required_coin_value = self.cfg.ask_fixed_amount * 1.1  # 10% buffer

            self.logger.info(
                f"{self.ticker} [BALANCE CHECK - ASK] "
                f"Available coins: {available_coin:.8f}, "
                f"Current price: {current_price:.2f}, "
                f"Coin value: {coin_value_usdt:.2f} USDT, "
                f"Required: {required_coin_value:.2f} USDT"
            )

            if coin_value_usdt < required_coin_value:
                raise RuntimeError(
                    f"Insufficient {self.ticker} for ASK side. "
                    f"Need {required_coin_value:.2f} USDT worth but only have {coin_value_usdt:.2f} USDT worth"
                )

            self.logger.info(f"{self.ticker} ✅ Balance validation passed")

        except RuntimeError:
            raise
        except Exception as e:
            self.logger.error(f"Balance check error: {e}")
            raise RuntimeError(f"Failed to check balance: {e}")

    def _ensure_clean_start(self):
        """Cancel all existing orders before starting"""
        self.logger.info(f"{self.ticker} [INIT] Initializing dual-side market maker...")
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

        raise RuntimeError(
            f"Order cleanup failed for {self.ticker} after {max_attempts} attempts"
        )

    def run_mm(self):
        """Main loop for dual-side market making"""

        # Validate balance
        self._validate_initial_balance()

        # Clean start
        self._ensure_clean_start()

        # Verify cleanup
        bid_orders = read_open_orders_side(self.driver, "bid")
        ask_orders = read_open_orders_side(self.driver, "ask")

        if bid_orders or ask_orders:
            self.logger.error(
                f"⚠️ CRITICAL: Orders still exist after cleanup! "
                f"BID: {len(bid_orders)}, ASK: {len(ask_orders)}"
            )
            return

        # Initial setup for both sides
        self.full_rebalance_both_sides()

        # Main loop
        while True:
            now = _now()

            # Rebalance check
            if now - self._last_rebalance_ts >= self.cfg.rebalance_interval_sec:
                self._sync_with_binance_both_sides()

            # Refill check
            if (not self._rebalance_lock) and (
                now - self._last_refill_ts >= self.cfg.refill_interval_sec
            ):
                self._refill_both_sides_if_needed()

            time.sleep(0.5)

    def full_rebalance_both_sides(self):
        """Full rebalance for both BID and ASK sides"""
        self._rebalance_lock = True
        try:
            # Get current price
            symbol = f"{self.ticker}USDT"
            self._anchor_price = get_binance_price(symbol)

            # Calculate adjustment
            if FLAG_ADJUSTMENT_ENABLE:
                self._price_adjustment = (
                    random.uniform(ADJUSTMENT_MIN, ADJUSTMENT_MAX) / 100.0
                )
            else:
                self._price_adjustment = 0.0

            self.logger.info(
                f"{self.ticker} [FULL REBALANCE - BOTH SIDES] "
                f"Binance={self._anchor_price:.3f} "
                f"Adjustment={self._price_adjustment*100:.2f}%"
            )

            # Cancel all existing orders
            if FLAG_REMOVE_EXCESS_ORDERS_ENABLE:
                self._remove_excess_orders_both_sides()

            # Setup both sides
            self._setup_both_sides()

            # Update timing
            self._last_rebalance_ts = _now()
            self._prev_anchor_price = self._anchor_price

        finally:
            self._rebalance_lock = False

    def _setup_both_sides(self):
        """Setup orders for both BID and ASK sides"""
        if self._anchor_price is None:
            return

        target_price = _normalize_price(self._anchor_price)

        # Step 1: Place BAIT orders (opposite side sweep preparation)
        self._place_bait_orders(target_price)

        # Step 2: SWEEP blocking orders
        self._sweep_blocking_orders(target_price)

        # Step 3: Place ANCHOR orders
        self._place_anchor_orders(target_price)

        # Step 4: Place LADDER orders
        self._place_ladder_orders_both_sides()

        self.logger.info(
            f"{self.ticker} ✅ Both sides setup complete at {target_price:.3f}"
        )

    def _place_bait_orders(self, target_price: float):
        """Place bait orders on opposite sides"""
        bait_qty = _normalize_qty(self.cfg.min_order_usdt / target_price)

        # BID bait on ASK side
        self.logger.info(
            f"{self.ticker} [BAIT-BID] ASK {target_price:.3f} qty={bait_qty:.8f}"
        )
        if not self._retry_order("ask", target_price, bait_qty, "BAIT-BID"):
            raise RuntimeError("BAIT-BID order failed")

        time.sleep(0.3)

        # ASK bait on BID side
        self.logger.info(
            f"{self.ticker} [BAIT-ASK] BID {target_price:.3f} qty={bait_qty:.8f}"
        )
        if not self._retry_order("bid", target_price, bait_qty, "BAIT-ASK"):
            raise RuntimeError("BAIT-ASK order failed")

        time.sleep(0.3)

    def _sweep_blocking_orders(self, target_price: float):
        """Sweep blocking orders on both sides"""

        # Sweep for BID side (buy from ASK orderbook)
        ask_blocking = self._get_blocking_orders("ask", target_price, is_bid=True)
        if ask_blocking:
            total_sweep_qty = sum(o.qty for o in ask_blocking)
            self.logger.info(
                f"{self.ticker} [SWEEP-BID] BID {total_sweep_qty:.8f} units at {target_price:.3f}"
            )
            if self._retry_order("bid", target_price, total_sweep_qty, "SWEEP-BID"):
                time.sleep(0.2)

        # Sweep for ASK side (sell to BID orderbook)
        bid_blocking = self._get_blocking_orders("bid", target_price, is_bid=False)
        if bid_blocking:
            total_sweep_qty = sum(o.qty for o in bid_blocking)
            self.logger.info(
                f"{self.ticker} [SWEEP-ASK] ASK {total_sweep_qty:.8f} units at {target_price:.3f}"
            )
            if self._retry_order("ask", target_price, total_sweep_qty, "SWEEP-ASK"):
                time.sleep(0.2)

    def _place_anchor_orders(self, target_price: float):
        """Place anchor orders on both sides"""

        # BID anchor
        try:
            usdt = self.cfg.bid_fixed_amount * self.cfg.anchor_order_budget_ratio
            qty = _normalize_qty(usdt / target_price)

            if qty > 0:
                self.logger.info(
                    f"{self.ticker} [ANCHOR-BID] BID {target_price:.3f} qty={qty:.8f}"
                )
                self._retry_order("bid", target_price, qty, "ANCHOR-BID")
                time.sleep(0.2)
        except Exception as e:
            self.logger.error(f"BID anchor failed: {e}")

        # ASK anchor
        try:
            coin_value = self.cfg.ask_fixed_amount
            total_coin_qty = coin_value / target_price
            qty = _normalize_qty(total_coin_qty * self.cfg.anchor_order_budget_ratio)

            if qty > 0:
                self.logger.info(
                    f"{self.ticker} [ANCHOR-ASK] ASK {target_price:.3f} qty={qty:.8f}"
                )
                self._retry_order("ask", target_price, qty, "ANCHOR-ASK")
                time.sleep(0.2)
        except Exception as e:
            self.logger.error(f"ASK anchor failed: {e}")

    def _place_ladder_orders_both_sides(self):
        """Place ladder orders for both sides"""

        # BID ladder
        bid_prices = self._calculate_ladder_prices("bid")
        self._place_ladder_orders_side("bid", bid_prices)

        # ASK ladder
        ask_prices = self._calculate_ladder_prices("ask")
        self._place_ladder_orders_side("ask", ask_prices)

    def _calculate_ladder_prices(self, side: str) -> List[float]:
        """Calculate ladder price levels"""
        if self._anchor_price is None:
            return []

        prices = []
        if side == "bid":
            for k in range(1, self.cfg.levels + 1):
                pk = self._anchor_price * ((1 - self._step) ** k)
                prices.append(_normalize_price(pk))
        else:  # ask
            for k in range(1, self.cfg.levels + 1):
                pk = self._anchor_price * ((1 + self._step) ** k)
                prices.append(_normalize_price(pk))

        return prices

    def _place_ladder_orders_side(self, side: str, prices: List[float]):
        """Place ladder orders for one side"""
        if not prices:
            return

        weights = _get_weights(len(prices), self.cfg.distribution_mode)

        if side == "bid":
            usdt = self.cfg.bid_fixed_amount * (1 - self.cfg.anchor_order_budget_ratio)

            for price, w in zip(prices, weights):
                budget = usdt * w
                qty = _normalize_qty(budget / price)
                if qty <= 0:
                    continue

                usdt_value = price * qty
                self.logger.info(
                    f"{self.ticker} [LADDER-BID] BID price={price:.3f} "
                    f"qty={qty:.8f} ≈{usdt_value:,.0f}usdt"
                )

                try:
                    place_limit_order(self.driver, "bid", price, qty)
                    _sleep_tiny()
                except Exception as e:
                    self.logger.error(f"❌ LADDER-BID failed at {price:.3f}: {e}")

        else:  # ask
            coin_value = self.cfg.ask_fixed_amount
            total_coin_qty = coin_value / self._anchor_price
            coin = total_coin_qty * (1 - self.cfg.anchor_order_budget_ratio)

            for price, w in zip(prices, weights):
                qty = _normalize_qty(coin * w)
                if qty <= 0:
                    continue

                usdt_value = price * qty
                self.logger.info(
                    f"{self.ticker} [LADDER-ASK] ASK price={price:.3f} "
                    f"qty={qty:.8f} ≈{usdt_value:,.0f}usdt"
                )

                try:
                    place_limit_order(self.driver, "ask", price, qty)
                    _sleep_tiny()
                except Exception as e:
                    self.logger.error(f"❌ LADDER-ASK failed at {price:.3f}: {e}")

    def _sync_with_binance_both_sides(self):
        """Sync with Binance price and decide rebalance strategy"""
        symbol = f"{self.ticker}USDT"

        try:
            new_price = get_binance_price(symbol)
        except Exception as e:
            self.logger.error(f"Failed to fetch Binance price: {e}")
            return

        if self._prev_anchor_price is None:
            self.full_rebalance_both_sides()
            return

        price_change = new_price - self._prev_anchor_price
        price_change_percent = (price_change / self._prev_anchor_price) * 100

        # Check orderbook status
        try:
            bid_orders = read_open_orders_side(self.driver, "bid")
            ask_orders = read_open_orders_side(self.driver, "ask")
            bid_empty = len(bid_orders) == 0
            ask_empty = len(ask_orders) == 0
        except Exception as e:
            self.logger.error(f"Failed to read orderbooks: {e}")
            return

        # Decision logic
        should_full_rebalance = False

        # If either side is empty, do full rebalance
        if bid_empty or ask_empty:
            self.logger.info(
                f"{self.ticker} [ORDERBOOK EMPTY] "
                f"BID: {len(bid_orders)}, ASK: {len(ask_orders)} → FULL REBALANCE"
            )
            should_full_rebalance = True

        # If price moved significantly, do full rebalance
        elif abs(price_change_percent) > 0.3:  # 0.3% threshold
            self.logger.info(
                f"{self.ticker} [PRICE CHANGE] "
                f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                f"({price_change:+.3f}, {price_change_percent:+.2f}%) → FULL REBALANCE"
            )
            should_full_rebalance = True

        else:
            self.logger.info(
                f"{self.ticker} [PRICE CHECK] "
                f"{self._prev_anchor_price:.3f} → {new_price:.3f} "
                f"({price_change:+.3f}, {price_change_percent:+.2f}%) → REFILL ONLY"
            )

        if should_full_rebalance:
            self.full_rebalance_both_sides()
        else:
            self._refill_orderbook_only_both_sides(new_price)

    def _refill_orderbook_only_both_sides(self, binance_price: float):
        """Refill orders without full rebalance"""
        self._anchor_price = binance_price
        self._prev_anchor_price = self._anchor_price

        self.logger.info(
            f"{self.ticker} [REFILL BOTH SIDES] Binance={self._anchor_price:.3f}"
        )

        # Refill BID side
        bid_prices = self._calculate_ladder_prices("bid")
        self._place_ladder_orders_side("bid", bid_prices)

        # Refill ASK side
        ask_prices = self._calculate_ladder_prices("ask")
        self._place_ladder_orders_side("ask", ask_prices)

        self._last_rebalance_ts = _now()

    def _refill_both_sides_if_needed(self):
        """Check and refill missing orders on both sides"""
        if self._anchor_price is None:
            self.full_rebalance_both_sides()
            return

        # Check BID side
        bid_orders = read_open_orders_side(self.driver, "bid")
        bid_need = self.cfg.levels - len(bid_orders)

        # Check ASK side
        ask_orders = read_open_orders_side(self.driver, "ask")
        ask_need = self.cfg.levels - len(ask_orders)

        if bid_need > 0 or ask_need > 0:
            self.logger.info(
                f"{self.ticker} [REFILL CHECK] Need BID:{bid_need} ASK:{ask_need}"
            )

            if bid_need > 0:
                self._refill_ladder_side("bid", bid_orders)

            if ask_need > 0:
                self._refill_ladder_side("ask", ask_orders)

            self._last_refill_ts = _now()

    def _refill_ladder_side(self, side: str, existing_orders):
        """Refill missing ladder orders for one side"""
        need = self.cfg.levels - len(existing_orders)

        if need <= 0:
            return

        # Calculate missing prices
        if side == "ask":
            if existing_orders:
                outer = max(o.price for o in existing_orders)
                new_prices = [
                    _normalize_price(outer * ((1 + self._step) ** i))
                    for i in range(1, need + 1)
                ]
            else:
                new_prices = self._calculate_ladder_prices("ask")[:need]
        else:  # bid
            if existing_orders:
                outer = min(o.price for o in existing_orders)
                new_prices = [
                    _normalize_price(outer * ((1 - self._step) ** i))
                    for i in range(1, need + 1)
                ]
            else:
                new_prices = self._calculate_ladder_prices("bid")[:need]

        # Place missing orders
        self._place_ladder_orders_side(side, new_prices)

    def _retry_order(
        self, side: str, price: float, qty: float, label: str, max_retries=3
    ):
        """Retry order placement"""
        for i in range(max_retries):
            try:
                success = place_limit_order(self.driver, side, price, qty)
                if success:
                    _sleep_tiny()
                    return True
                else:
                    self.logger.warning(f"⚠️ {label} FAILED ({i+1}/{max_retries})")
                    if i < max_retries - 1:
                        time.sleep(1)
            except Exception as e:
                self.logger.warning(f"⚠️ {label} FAILED ({i+1}/{max_retries}): {e}")
                if i < max_retries - 1:
                    time.sleep(1)

        self.logger.error(f"❌ {label} FAILED after {max_retries} retries")
        return False

    def _get_blocking_orders(self, side: str, target_price: float, is_bid: bool):
        """Get blocking orders from orderbook"""
        orders = read_orderbook(self.driver, side)
        if is_bid:
            blocking = [o for o in orders if o.price < target_price]
        else:
            blocking = [o for o in orders if o.price > target_price]

        if blocking:
            self.logger.info(
                f"{self.ticker} [BLOCKING] Found {len(blocking)} on {side.upper()}"
            )
        return blocking

    def _remove_excess_orders_both_sides(self):
        """Remove excess orders from both sides"""
        bid_orders = read_open_orders_side(self.driver, "bid")
        ask_orders = read_open_orders_side(self.driver, "ask")

        if len(bid_orders) > self.cfg.levels or len(ask_orders) > self.cfg.levels:
            self.logger.info(
                f"{self.ticker} [REMOVE EXCESS] "
                f"BID: {len(bid_orders)}/{self.cfg.levels}, "
                f"ASK: {len(ask_orders)}/{self.cfg.levels}"
            )
            cancel_all_open_orders(self.driver)
            time.sleep(1)


def run_dual_side_mm(vic_url: str, ticker: str, bid_amount: float, ask_amount: float):
    """Run dual-side market maker"""
    cfg = _build_dual_cfg(bid_amount, ask_amount)
    driver = init_driver()

    try:
        driver.get(f"{vic_url}/account/login")
        validate_login_or_exit(driver=driver, mode=5)
        driver.get(_vic_trade_url(vic_url, ticker))

        # Wait for page load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, "user_base_trans"))
        )

        # Run dual-side engine
        engine = DualSideMMEngine(driver=driver, cfg=cfg, ticker=ticker)
        engine.run_mm()

    except KeyboardInterrupt:
        print("\n[INFO] Dual-side MM stopped by user (KeyboardInterrupt)")
    except Exception as e:
        print(f"\n[ERROR] Dual-side MM crashed: {type(e).__name__} - {e}")
        import traceback

        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except (WebDriverException, Exception) as e:
            print(f"[WARNING] Error during driver cleanup: {e}")
        print("[INFO] Driver shutdown complete.")
