# driver_utils.py
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH")

if not CHROME_DRIVER_PATH:
    raise RuntimeError("[ERROR] CHROME_DRIVER_PATH is not set in .env file.")


def init_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--start-maximized")

    service = Service(CHROME_DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    return driver
