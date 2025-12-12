from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

import time
import os
import random
import requests

# -------------------------------------------------
#  환경 설정
# -------------------------------------------------
load_dotenv()  # .env 파일 로드
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH")
VICTORIA_URL = os.getenv("VICTORIA_URL")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
ORDERBOOK_REFRESH_INTERVAL = 2.5  # seconds


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


DISCOUNT_MIN = get_env_float("DISCOUNT_MIN")
DISCOUNT_MAX = get_env_float("DISCOUNT_MAX")
FOLLOW_UPDATE_SEC = get_env_int("FOLLOW_UPDATE_SEC")


# -------------------------------------------------
#  드라이버 초기화
# -------------------------------------------------
def init_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    service = Service(CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


# -------------------------------------------------
#  호가 행(Row) 데이터 파싱
# -------------------------------------------------
def parse_rows(rows):
    prices, amounts = [], []
    for row in rows:
        try:
            price = (
                row.find_element(By.CSS_SELECTOR, ".col-price")
                .text.strip()
                .replace(",", "")
            )
            amount = (
                row.find_element(By.CSS_SELECTOR, ".col-amount")
                .text.strip()
                .replace(",", "")
            )
            if price and amount and price != "-" and amount != "-":
                prices.append(float(price))
                amounts.append(float(amount))
        except Exception:
            continue
    return prices, amounts


# -------------------------------------------------
#  콘솔 출력 함수
# -------------------------------------------------
def print_orderbook(coin_name, coin_ticker, asks, bids):
    print(f"┌───────────── {time.strftime('%H:%M:%S')}  {coin_ticker} ─────────────┐")

    # 매도 (Ask)
    print("\n            🟦 매도 호가 (Ask)\n")
    for i, (price, amount) in enumerate(asks[:10], 1):
        print(f" {11 - i:2d}호가 │ {price:>14,.8f} │ {amount:>14,.8f}")

    # 매수 (Bid)
    print("\n            🔴 매수 호가 (Bid)\n")
    for i, (price, amount) in enumerate(bids, 1):
        print(f" {i:2d}호가 │ {price:>14,.8f} │ {amount:>14,.8f}")

    print("\n└" + "─" * 41 + "┘\n")


# -------------------------------------------------
#  실시간 호가창 루프 (모드 1)
# -------------------------------------------------
def run_orderbook(driver):
    driver.get(f"{VICTORIA_URL}/trade")

    # 호가창 로딩 대기
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "a.bidding-table-rows"))
    )

    while True:
        try:
            if not driver.window_handles:
                print("\n브라우저가 닫혔습니다. 프로그램을 종료합니다.")
                break

            ask_rows = driver.find_elements(
                By.CSS_SELECTOR, "#mCSB_2_container > a.bidding-table-rows"
            )
            bid_rows = driver.find_elements(
                By.CSS_SELECTOR, "#mCSB_3_container > a.bidding-table-rows"
            )

            ask_prices, ask_amounts = parse_rows(ask_rows)
            bid_prices, bid_amounts = parse_rows(bid_rows)

            coin_name = driver.find_element(
                By.CSS_SELECTOR, "b.pair-title"
            ).text.strip()

            ticker_text = driver.find_element(By.CSS_SELECTOR, "span.unit").text.strip()
            coin_ticker = ticker_text.replace("/USDT", "")

            if len(ask_prices) < 10 or len(bid_prices) < 10:
                time.sleep(0.5)
                continue

            # 매도/매수 정렬
            asks_sorted = sorted(zip(ask_prices, ask_amounts), reverse=True)
            bids_sorted = sorted(zip(bid_prices, bid_amounts), reverse=True)

            asks = asks_sorted[-10:]  # 낮은 매도 10개
            bids = bids_sorted[:10]  # 높은 매수 10개

            print_orderbook(coin_name, coin_ticker, asks, bids)
            time.sleep(ORDERBOOK_REFRESH_INTERVAL)

        except KeyboardInterrupt:
            print("\n사용자에 의해 중단됨.")
            break
        except Exception as e:
            print("오류 발생:", e)
            time.sleep(1)


# -------------------------------------------------
#   Binance 가격 가져오기 (공개 API, 키 필요 없음)
# -------------------------------------------------
def get_binance_price(symbol: str) -> float:
    url = "https://api.binance.com/api/v3/ticker/price"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return float(r.json()["price"])


# -------------------------------------------------
#   VictoriaEX 현재 심볼을 Binance 심볼로 변환
# -------------------------------------------------
def get_victoria_binance_symbol(driver) -> str:
    unit_text = driver.find_element(By.CSS_SELECTOR, "span.unit").text.strip()
    return unit_text.replace("/", "").upper()


# -------------------------------------------------
#  바이낸스 가격 추종 모드 (모드 2) - 지금은 드라이런(출력만)
# -------------------------------------------------
def run_follow_binance(driver):
    driver.get(f"{VICTORIA_URL}/trade")

    # trade 페이지 기본 로딩 대기(최소)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "b.pair-title"))
    )

    print("\n[모드 2] 바이낸스 가격 추종")

    while True:
        try:
            if not driver.window_handles:
                print("\n브라우저가 닫혔습니다. 프로그램 종료.")
                break

            symbol = get_victoria_binance_symbol(
                driver
            )  # 매 루프마다 현재 선택 코인 읽기
            binance_price = get_binance_price(symbol)

            discount = random.uniform(DISCOUNT_MIN, DISCOUNT_MAX)
            target_price = binance_price * (1 - discount)

            print(
                f"[{time.strftime('%H:%M:%S')}] Binance {symbol}={binance_price:.2f} | "
                f"target(-{discount*100:.3f}%)={target_price:.2f}"
            )

            # TODO: 여기서 VictoriaEX에 주문 넣는 함수 호출로 확장
            # place_victoria_order(driver, target_price, ...)

            time.sleep(FOLLOW_UPDATE_SEC)

        except KeyboardInterrupt:
            print("\n사용자에 의해 중단됨.")
            break
        except Exception as e:
            print("[추종모드 오류]:", e)
            time.sleep(2)


# -------------------------------------------------
#  main() — 실행 시작점
# -------------------------------------------------
def main():
    driver = init_driver()
    try:
        driver.get(f"{VICTORIA_URL}/account/login")
        print("\n" + "=" * 45)
        print("         💎 VictoriaEX 연결 완료 💎")
        print("  로그인 후 Enter 키를 눌러 계속 진행하세요.")
        print("=" * 45 + "\n")
        input()

        print("\n실행 모드 선택:")
        print("1) VictoriaEX 호가창 출력")
        print("2) Binance BTCUSDT 추종 모드")
        mode = input("선택(1~2): ").strip()

        if mode == "1":
            run_orderbook(driver)
        elif mode == "2":
            run_follow_binance(driver)
        else:
            print("잘못된 입력입니다. 1 또는 2를 입력하세요.")

    finally:
        driver.quit()
        print("드라이버 종료 완료.")


# -------------------------------------------------
# 프로그램 실행
# -------------------------------------------------
if __name__ == "__main__":
    main()
