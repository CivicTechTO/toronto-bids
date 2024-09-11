import re
from time import sleep

from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait

ARIBA_BASE_URL = "https://service.ariba.com/Discovery.aw/ad/profile"


class Ariba(Chrome):
    def __init__(self, ariba_discovery_profile_key, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ariba_discovery_profile_key = ariba_discovery_profile_key
        self.login()

    def patiently_click(self, button, wait_after=0):
        print(f"Looking for {button}")
        WebDriverWait(self, timeout=120).until(ec.element_to_be_clickable((By.XPATH, button)))
        print(f"Clicking {button}")
        self.find_element(By.XPATH, button).click()
        if wait_after > 0:
            print(f"Waiting {wait_after} seconds after clicking {button}")
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

    def login(self):
        self.home(profile_key=self.ariba_discovery_profile_key)
        sleep(10)
        return

    def home(self, profile_key):
        key = profile_key if not profile_key else self.ariba_discovery_profile_key
        self.get(f"{ARIBA_BASE_URL}?key={key}")
