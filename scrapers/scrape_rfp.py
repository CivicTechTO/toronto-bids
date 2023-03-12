import datetime as dt
import pandas as pd
import re
from pathlib import Path
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.remote.errorhandler import NoSuchElementException
from seleniumwire import webdriver
from time import sleep
from webdriver_manager.chrome import ChromeDriverManager
import os

ARIBA_URL = "https://service.ariba.com/Discovery.aw/ad/profile?key=AN01050912625#b0"
DOWNLOAD_DIRECTORY = os.path.join(os.getcwd(), 'scrapers', 'download')

if not os.path.exists(DOWNLOAD_DIRECTORY):
    os.mkdir(DOWNLOAD_DIRECTORY)

driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()))

clicked = set()

# What's this?
if Path('data_summary.csv').exists():
    df = pd.read_csv('data_summary.csv')
    for title in df.title.values:
        if title == title:
            title = title.replace('...', '')
            clicked.add(title[0:100])

# This is not needed?
# has_clicked = False

def patiently_click(driver, button, wait_after=0):
    WebDriverWait(driver, timeout=60).until(EC.element_to_be_clickable((By.XPATH, button)))
    driver.find_element(By.XPATH, button).click()
    if wait_after > 0:
        sleep(wait_after)


def wait_for_download(command, max_wait=1200):
    initial_length = len(list(DOWNLOAD_DIRECTORY.iterdir()))
    command()
    total_wait = 0
    while len(list(DOWNLOAD_DIRECTORY.iterdir())) == initial_length:
        sleep(15)
        total_wait += 15
        if total_wait > max_wait:
            return False
    return True


def patiently_find_regex(driver, regex):
    attempts = 0
    results = []
    while len(results) == 0 and attempts < 30:
        sleep(15)
        results = re.findall(regex, driver.page_source)
        attempts += 1
    if len(results) == 0:
        return None
    return results[0]


def count_directory_files(root: Path):
    if not root.exists():
        return 0
    return len(list(root.iterdir()))


def main_loop(has_clicked=False):
    while not has_clicked:
        elements = driver.find_elements(By.CLASS_NAME, 'ADTableBodyWhite')
        elements += driver.find_elements(By.CLASS_NAME, 'ADHiliteBlock')
        for element in elements:
            try:
                title = element.find_element(By.CLASS_NAME, 'QuoteSearchResultTitle')
            except NoSuchElementException:
                continue
            title_text = title.text
            if title_text[0:100] in clicked:
                continue
            
            print(f'Accessing {title.text}')
            try:
                date = element.find_elements(By.CLASS_NAME, 'paddingRight5')[2].text
                request_expired = dt.datetime.strptime(date[:-4], '%d %b %Y %I:%M %p') < dt.datetime.now()
                print(f'\tdate: {date}')

            except IndexError:
                request_expired = True
                print('\tNo date found')

            clicked.add(title_text[0:100])
            title.click()
            has_clicked = True

            document_id = patiently_find_regex(driver, '(Doc\d{10})')
            print(f'\tDocument id is {document_id}')

            html_exists = Path(f'{REPO_DIRECTORY}/data/{document_id}.html').exists() or Path(
                f'{REPO_DIRECTORY}/data/{document_id}/{document_id}.html'
            ).exists()
            zip_exists = Path(f'{REPO_DIRECTORY}/data/{document_id}.zip').exists() or count_directory_files(Path(
                f'{REPO_DIRECTORY}/data/{document_id}'
            )) > 1
            
            print('\tHTML exists' if html_exists else '\tHTML does not exist')

            if zip_exists:
                print('\tZip exists')
            elif not request_expired:
                print('\tZip does not exist')
            else:
                print('\tZip does not exist, but RFP is expired')

            if not html_exists:
                with open(f'{REPO_DIRECTORY}/data/{document_id}.html', 'w') as f:
                    f.write(driver.page_source)
            if (not zip_exists) and (not request_expired):
                patiently_click(driver, '//*[@id="_hfdr9c"]')  #respond to posting
                patiently_click(driver, '//*[@id="_xjqay"]')  #download content
                patiently_click(driver, '//*[@id="_hgesab"]', wait_after=15)  #click download attachment
                patiently_click(driver, '//*[@id="_h_l$m"]/span/div/label', wait_after=5)  #click select all
                wait_for_download(
                    lambda: patiently_click(driver, '//*[@id="_5wq_j"]')
                )  #download attachments (for real)
            driver.get("https://service.ariba.com/Discovery.aw/ad/profile?key=AN01050912625#b0")
            sleep(2)
            break
        if not has_clicked:
            patiently_click(driver, '//*[@id="next"]', wait_after=5)


if __name__ == "__main__":
    try:
        main_loop()
    except:
        sleep(5 * 1)
        driver.get("https://service.ariba.com/Discovery.aw/ad/profile?key=AN01050912625#b0")