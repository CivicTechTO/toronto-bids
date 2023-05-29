from selenium.webdriver import Chrome
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException

from time import sleep
from secret_manager import Keychain
import re

ARIBA_BASE_URL = "https://service.ariba.com/Discovery.aw/ad/profile"


class Ariba(Chrome):
    def __init__(self, ariba_discovery_profile_key, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ariba_discovery_profile_key = ariba_discovery_profile_key
        self.login(Keychain())

    def patiently_click(self, button, wait_after=0):
        WebDriverWait(self, timeout=60).until(
            EC.element_to_be_clickable((By.XPATH, button))
        )
        self.find_element(By.XPATH, button).click()
        if wait_after > 0:
            sleep(wait_after)

    def patiently_find_regex(self, regex):
        max_wait = 1200
        total_wait = 0
        results = []
        while len(results) == 0 and total_wait < max_wait:
            sleep(1)
            total_wait += 1
            results = re.findall(regex, self.page_source)
        if len(results) == 0:
            return None
        return results[0]

    def is_logged_in(self):
        login = self.find_elements(By.CSS_SELECTOR, ".sap-icon--log")
        return len(login) == 0

    def login(self, keychain: Keychain):
        self.home(profile_key=self.ariba_discovery_profile_key)
        WebDriverWait(self, timeout=60).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".sap-icon--log"))
        )
        self.find_element(By.CSS_SELECTOR, ".sap-icon--log").click()
        # Wait until username and password fields are visible
        WebDriverWait(self, timeout=60).until(
            EC.visibility_of_element_located(
                (By.NAME, "UserName") and (By.NAME, "Password")
            )
        )
        self.find_element(By.NAME, "UserName").send_keys(
            keychain.get_secret("ARIBAUSERNAME")
        )
        self.find_element(By.NAME, "Password").send_keys(
            keychain.get_secret("ARIBAPASSWORD")
        )
        try:
            self.find_element(By.NAME, "Password").send_keys(Keys.ENTER)
        except NoSuchElementException as e:
            # This might be okay. On MacOS, it seems like just entering the password causes the login to happen.
            sleep(2)
            # If we're still not logged in, raise the exception
            if not self.is_logged_in():
                raise e
        sleep(2)
        self.home(profile_key=self.ariba_discovery_profile_key)

    def home(self, profile_key):
        key = profile_key if not profile_key else self.ariba_discovery_profile_key
        self.get(f"{ARIBA_BASE_URL}?key={key}")
