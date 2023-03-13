from selenium.webdriver import Chrome
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from time import sleep
from pathlib import Path
import re


class Ariba(Chrome):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.login()

    def patiently_click(self, button, wait_after=0):
        WebDriverWait(self, timeout=60).until(EC.element_to_be_clickable((By.XPATH, button)))
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

    def login(self):
        username_path = Path('username.key')
        password_path = Path('password.key')
        if not username_path.exists() or not password_path.exists():
            raise FileNotFoundError('username.key or password.key not found')
        self.home()
        WebDriverWait(self, timeout=60).until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".sap-icon--log")))
        self.find_element(By.CSS_SELECTOR, ".sap-icon--log").click()
        # Wait until username and password fields are visible
        WebDriverWait(self, timeout=60).until(
            EC.visibility_of_element_located(
                (By.NAME, "UserName") and (By.NAME, "Password")
            )
        )
        with open(username_path,'r') as f:
            self.find_element(By.NAME, "UserName").send_keys(f.read())
        with open(password_path,'r') as f:
            self.find_element(By.NAME, "Password").send_keys(f.read())
        self.find_element(By.NAME, "Password").send_keys(Keys.ENTER)
        sleep(2)
        self.home()

    def home(self):
        self.get("https://service.ariba.com/Discovery.aw/ad/profile?key=AN01050912625#b0")
