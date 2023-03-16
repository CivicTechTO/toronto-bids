import datetime as dt
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.errorhandler import NoSuchElementException
from ariba_driver import Ariba
from time import sleep
from webdriver_manager.chrome import ChromeDriverManager
from filemanage import extract_zip_and_move_html, move_pdfs, parse_html, delete_duplicates

# Working directory
REPO_DIRECTORY = Path.cwd()
# System default download directory
DOWNLOAD_DIRECTORY = REPO_DIRECTORY / 'downloads'
DATA_DIRECTORY = REPO_DIRECTORY / 'data'


def wait_for_download(command, max_wait=1200) -> bool:
    initial_length = len(list(DOWNLOAD_DIRECTORY.iterdir()))
    command()
    total_wait = 0
    while len(list(DOWNLOAD_DIRECTORY.iterdir())) == initial_length:
        sleep(1)
        total_wait += 1
        if total_wait > max_wait:
            return False
    return True


def count_directory_files(root: Path) -> int:
    if not root.exists():
        return 0
    return len(list(root.iterdir()))


def main_loop(has_clicked: bool = False) -> bool:
    while not has_clicked:  # We loop through RFPs until we find one we want to click on
        elements = driver.find_elements(By.CLASS_NAME,
                                        'ADTableBodyWhite')  # Class name for open RFPs (and some closed ones!)
        elements += driver.find_elements(By.CLASS_NAME, 'ADHiliteBlock')  # Class name for closed RFPs (exclusively)
        for element in elements:
            try:
                title = element.find_element(By.CLASS_NAME, 'QuoteSearchResultTitle')  # Title is hyperlink
            except NoSuchElementException:
                continue
            title_text = title.text
            if title_text in clicked:
                continue

            print(f'{title.text}')

            # Attempt to parse expiry date, so we can see if the RFP is open
            date = element.find_elements(By.CLASS_NAME, 'paddingRight5')
            if len(date) < 3:
                request_expired = True
                print('\tNo date found')
            else:
                date = date[2].text
                request_expired = dt.datetime.strptime(date[:-4], '%d %b %Y %I:%M %p') < dt.datetime.now()
                print(f'\tdate: {date}')

            clicked.add(title_text)
            title.click()
            has_clicked = True

            # Now we've moved from the listing page to the RFP page. First thing is to identify the doc number
            document_id = driver.patiently_find_regex('(Doc\d{10})')
            print(f'\tDocument id is {document_id}')

            # Now we check if there are any PDFs to download on the listing page
            noip = driver.find_elements(By.XPATH, '//a[contains(text(),".pdf")]')
            for link in noip:
                print(f'\tPDF found, downloading {link.text}')
                wait_for_download(lambda: link.click())

            # Check to see if we've already downloaded the raw HTML, and the attachments
            html_exists = Path(f'{DATA_DIRECTORY}/{document_id}.html').exists() or Path(
                f'{DATA_DIRECTORY}/{document_id}/{document_id}.html'
            ).exists()

            # Zip might exist as a zip file, or as a directory - if it's the latter, we need to check that there's
            # more than just the HTML file
            zip_exists = Path(f'{DATA_DIRECTORY}/{document_id}.zip').exists() or count_directory_files(Path(
                f'{DATA_DIRECTORY}/{document_id}'
            )) > 1

            # Print the results of our checks
            print('\tHTML exists' if html_exists else '\tHTML does not exist')

            if zip_exists:
                print('\tZip exists')
            elif not request_expired:
                print('\tZip does not exist')
            else:
                print('\tZip does not exist, but RFP is expired')

            # If we haven't already downloaded the HTML, download it now
            if not html_exists:
                with open(f'{DATA_DIRECTORY}/{document_id}.html', 'w') as f:
                    f.write(driver.page_source)
            if (not zip_exists) and (not request_expired):
                # If we don't have the attachments and the RFP is still open, download them
                driver.patiently_click('//*[@id="_hfdr9c"]')  # respond to posting
                driver.patiently_click('//*[@id="_xjqay"]')  # download content
                driver.patiently_click('//*[@id="_hgesab"]', wait_after=15)  # click download attachment
                driver.patiently_click('//*[@id="_h_l$m"]/span/div/label', wait_after=5)  # click select all
                wait_for_download(
                    lambda: driver.patiently_click('//*[@id="_5wq_j"]')
                )  # download attachments (for real)
                driver.home()
            else:
                driver.patiently_click('//a[contains(text(),"Back to Search Results")]', wait_after=5)

            return False  # False because we aren't finished
        if not has_clicked:
            # if we didn't find an RFP on this page, we should go to the next page.
            # First, check if the next page button is clickable
            next_button = driver.find_element(By.XPATH, '//*[@id="next"]')
            # If next button contains a div with the id "noLink", it's not clickable
            if next_button.find_elements(By.XPATH, 'div[@id="noLink"]'):
                print('No more RFPs to click on')
                return True  # True because we are finished
            else:
                # If it is clickable, click it
                driver.patiently_click('//*[@id="next"]', wait_after=5)


if __name__ == '__main__':
    Path(DOWNLOAD_DIRECTORY).mkdir(exist_ok=True)
    Path(DATA_DIRECTORY).mkdir(exist_ok=True)
    finished = False
    clicked = set()
    chrome_options =  webdriver.ChromeOptions()
    prefs = {'download.default_directory' : str(DOWNLOAD_DIRECTORY)}
    chrome_options.add_experimental_option('prefs', prefs)
    driver = Ariba(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)

    while not finished:
        try:
            finished = main_loop()
        except Exception as e:
            print(e)
            # Check if the issue is that we aren't logged in
            if not driver.is_logged_in():
                driver.login()
            else:
                driver.quit()
                driver = Ariba(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)

    # Move zips from download directory to repo's data directory
    for file in DOWNLOAD_DIRECTORY.iterdir():
        if file.suffix == '.zip':
            file.rename(DATA_DIRECTORY / file.name)

    extract_zip_and_move_html(DATA_DIRECTORY)
    move_pdfs(DOWNLOAD_DIRECTORY, DATA_DIRECTORY)

    parse_html(DATA_DIRECTORY).to_csv('metadata.csv', index=False)

    delete_duplicates(DATA_DIRECTORY)
