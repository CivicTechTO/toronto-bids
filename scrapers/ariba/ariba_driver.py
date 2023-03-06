from selenium.webdriver import Chrome
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.common.by import By
from time import sleep
import re


class Ariba(Chrome):

    def patiently_click(self, button, wait_after=0):
        WebDriverWait(self, timeout=60).until(EC.element_to_be_clickable((By.XPATH, button)))
        self.find_element(By.XPATH, button).click()
        if wait_after > 0:
            sleep(wait_after)

    def patiently_find_regex(self, regex):
        attempts = 0
        results = []
        while len(results) == 0 and attempts < 30:
            sleep(15)
            results = re.findall(regex, self.page_source)
            attempts += 1
        if len(results) == 0:
            return None
        return results[0]
