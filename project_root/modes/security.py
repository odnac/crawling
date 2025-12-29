# security.py
import os
import getpass
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from config import FLAG_LOGIN_ENABLE


def check_password():
    system_pw = os.getenv("APP_PASSWORD")
    if not system_pw:
        print("\n⚠️ The environment variable (APP_PASSWORD) is not set.")
        return False

    user_pw = getpass.getpass("\n🔒 Enter password to start: ")

    if user_pw != system_pw:
        print("\n❌ Wrong password. Access denied.\n\n")
        return False

    print("✅ Access granted!")
    return True


def check_login_success(driver) -> bool:
    # if
    if FLAG_LOGIN_ENABLE:
        try:
            driver.find_element(
                By.CSS_SELECTOR,
                'li.nav-item[data-access="login"] button.dropdown-toggle',
            )
            return True

        except NoSuchElementException:
            return False
    # else
    else:
        return True
    # endif
