import datetime as dt
import filecmp
import platform
from hashlib import sha256
from pathlib import Path
import pandas as pd
from selenium import webdriver
from selenium.webdriver import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.errorhandler import NoSuchElementException
from ariba_driver import Ariba
from time import sleep, time
from webdriver_manager.chrome import ChromeDriverManager
from filemanage import (
    extract_zip_and_move_html,
    move_pdfs,
    parse_html,
    delete_duplicates,
)
from open_data import get_open_data
from transmit_json import transmit_json
import argparse
from secret_manager import Keychain

from slack import Slack

# Working directory
REPO_DIRECTORY = Path.cwd()
# System default download directory
DOWNLOAD_DIRECTORY = REPO_DIRECTORY / "files" / "downloads"
OPEN_DATA_DIRECTORY = REPO_DIRECTORY / "files" / "open_data"
ARIBA_DATA_DIRECTORY = REPO_DIRECTORY / "files" / "ariba_data"


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


def parse_date_text(date_text: str) -> dt.datetime:
    # Date text is either in format 7 Jun 2022, or 7 Jun 2022 12:00 AM PDT
    # We only care about the date, so we split on space and take the first three elements
    try:
        date_text = date_text.split(" ")[:3]
        return dt.datetime.strptime(" ".join(date_text), "%d %b %Y")
    except:
        # Return 1/1/1970 if we can't parse the date
        return dt.datetime(1970, 1, 1)


def main_loop(
    scraper_config,
    has_clicked: bool = False,
    closing_soon: bool = True,
    download_everything: bool = False,
) -> bool:
    while not has_clicked:  # We loop through RFPs until we find one we want to click on
        elements = driver.find_elements(
            By.CLASS_NAME, "ADTableBodyWhite"
        )  # Class name for open RFPs (and some closed ones!)
        elements += driver.find_elements(
            By.CLASS_NAME, "ADHiliteBlock"
        )  # Class name for closed RFPs (exclusively)
        for element in elements:
            try:
                title = element.find_element(
                    By.CLASS_NAME, "QuoteSearchResultTitle"
                )  # Title is hyperlink
            except NoSuchElementException:
                continue
            title_text = title.text
            if title_text in clicked:
                continue

            # Attempt to find expiry date, so we can see if it's expired
            # Each element has two dates, one for opening and one for closing
            # If the RFP is closed, there will only be one date
            date = element.find_elements(By.CLASS_NAME, "paddingRight5")
            if len(date) == 0:
                continue

            most_recent_date = parse_date_text(date[0].text)
            for d in date:
                parsed_date = parse_date_text(d.text)
                if parsed_date > most_recent_date:
                    most_recent_date = parsed_date

            if most_recent_date < dt.datetime.now():
                request_expired = True
                if closing_soon:
                    continue
            elif most_recent_date < dt.datetime.now() + dt.timedelta(days=7):
                request_expired = False
            else:
                request_expired = False
                if closing_soon:
                    continue

            thread = slack.post_log(f"{title.text}")
            slack.post_log(
                f"\tLikely expiry date: {most_recent_date.strftime('%d %b %Y')}", thread
            )

            clicked.add(title_text)
            title.click()
            has_clicked = True
            # Now we've moved from the listing page to the RFP page. First thing is to identify the doc number
            driver.patiently_find_regex("Back to Search Results")
            document_id = driver.patiently_find_regex("(Doc\d{10})")
            slack.post_log(f"\tDocument id is {document_id}", thread)

            # Now we check if there are any PDFs to download on the listing page
            noip = driver.find_elements(By.XPATH, '//a[contains(text(),".pdf")]')
            for link in noip:
                slack.post_log(f"\tPDF found, downloading {link.text}", thread)
                wait_for_download(lambda: link.click())

            # Check to see if we've already downloaded the raw HTML, and the attachments
            html_exists = (
                (
                    Path(f"{ARIBA_DATA_DIRECTORY}/{document_id}.html").exists()
                    or Path(
                        f"{ARIBA_DATA_DIRECTORY}/{document_id}/{document_id}.html"
                    ).exists()
                )
                if not download_everything
                else False
            )

            # Zip might exist as a zip file, or as a directory - if it's the latter, we need to check that there's
            # more than just the HTML file
            zip_exists = (
                (
                    Path(f"{ARIBA_DATA_DIRECTORY}/{document_id}.zip").exists()
                    or count_directory_files(
                        Path(f"{ARIBA_DATA_DIRECTORY}/{document_id}")
                    )
                    > 1
                )
                if not download_everything
                else False
            )

            # Print the results of our checks
            slack.post_log(
                "\tHTML archived" if html_exists else "\tHTML not yet archived...",
                thread,
            )

            if zip_exists:
                slack.post_log("\tAttachments already archived", thread)
            elif not request_expired:
                slack.post_log("\tAttachments not yet archived...", thread)
            else:
                slack.post_log("\tAttachments not archived, but RFP is expired", thread)

            # If we haven't already downloaded the HTML, download it now
            if not html_exists:
                with open(f"{ARIBA_DATA_DIRECTORY}/{document_id}.html", "w") as f:
                    f.write(driver.page_source)
            if (not zip_exists) and (not request_expired):
                # If we don't have the attachments and the RFP is still open, download them
                driver.patiently_click("//a[contains(@class, 'adsmallbutton') and contains(@class, 'adbuttonblock') and contains(@class, 'buttonRightPadding')]", wait_after=3)


                # If we aren't logged in yet, we will now get a box asking us to log in. There will be two elements
                # we care about: <input value=" Enter Username" onblur="ariba.Handlers.hTextBlur(this, event)" _pl="
                # Enter Username" bh="TF" size="30" maxlength="100" class="ph tf tfW" type="text" name="UserName">
                # <input value="" onblur="ariba.Handlers.hPassBlur(this, event)" onfocus="ariba.Handlers.hPassFocus(
                # this, event)" bh="PF" autocomplete="off" size="30" maxlength="48" class="tf tfW" type="password"
                # name="Password">

                try:
                    # Try to locate the element by multiple attributes
                    username_element = driver.find_element(By.XPATH, "//input[@name='UserName'][@type='text'][@maxlength='100']")

                    # Additional check for class
                    if 'tfW' in username_element.get_attribute('class'):
                        username_element.send_keys(keychain.get_secret("ARIBAUSERNAME"))
                    else:
                        print("Element found but class does not match.")

                    # Locate the element by multiple attributes
                    password_element = driver.find_element(By.XPATH, "//input[@name='Password'][@type='password'][@maxlength='48']")

                    # Additional check for class
                    if 'tfW' in password_element.get_attribute('class'):
                        password_element.send_keys(keychain.get_secret("ARIBAPASSWORD"))
                        password_element.send_keys(Keys.ENTER)
                    else:
                        print("Element found but class does not match.")

                except NoSuchElementException:
                    print("Seems like we are logged in.")

                driver.patiently_click('//*[@id="_xjqay"]')  # download content
                driver.patiently_click(
                    '//*[@id="_hgesab"]', wait_after=15
                )  # click download attachment
                driver.patiently_click(
                    '//*[@id="_h_l$m"]/span/div/label', wait_after=5
                )  # click select all
                wait_for_download(
                    lambda: driver.patiently_click('//*[@id="_5wq_j"]')
                )  # download attachments (for real)
                driver.home(scraper_config.get_config("aribaDiscoveryProfileKey"))
            else:
                driver.patiently_click(
                    '//a[contains(text(),"Back to Search Results")]', wait_after=5
                )

            slack.post_log(f"Downloaded successfully!", thread)

            return False  # False because we aren't finished
        if not has_clicked:
            # if we didn't find an RFP on this page, we should go to the next page.
            # First, check if the next page button is clickable
            next_button = driver.find_element(By.XPATH, '//*[@id="next"]')
            # If next button contains a div with the id "noLink", it's not clickable
            if next_button.find_elements(By.XPATH, 'div[@id="noLink"]'):
                slack.post_log("No more RFPs to click on")
                return True  # True because we are finished
            else:
                # If it is clickable, click it
                driver.patiently_click('//*[@id="next"]', wait_after=5)


if __name__ == "__main__":
    start_time = time()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-scraper",
        action="store_true",
        help="Skip the scraper and just process the data",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force the scraper to run even if the data hasn't changed",
    )
    parser.add_argument(
        "--scrape-all",
        action="store_true",
        help="Include all RFPs, not just those closing soon",
    )
    parser.add_argument(
        "--download-everything",
        action="store_true",
        help="Download all attachments even if they already exist",
    )
    parser.add_argument(
        "--no-slack",
        action="store_true",
        help="Don't post updates to Slack",
    )
    parser.add_argument(
        "--ariba-password",
        action="store",
        help="Override the Ariba password stored in the keychain"
    )
    parser.add_argument(
        "--ariba-username",
        action="store",
        help="Override the Ariba username stored in the keychain"
    )
    parser.add_argument(
        "--azure-storage-key",
        action="store",
        help="Override the Azure storage key stored in the keychain"
    )
    parser.add_argument(
        "--azure-storage-account",
        action="store",
        help="Override the Azure storage account name"
    )
    parser.add_argument(
        "--slack-key",
        action="store",
        help="Override the Slack key stored in the keychain"
    )
    args = parser.parse_args()
    Path(DOWNLOAD_DIRECTORY).mkdir(parents=True, exist_ok=True)
    Path(ARIBA_DATA_DIRECTORY).mkdir(parents=True, exist_ok=True)
    Path(OPEN_DATA_DIRECTORY).mkdir(parents=True, exist_ok=True)

    keychain = Keychain()
    if args.ariba_password:
        keychain.cache["ARIBAPASSWORD"] = args.ariba_password
    if args.ariba_username:
        keychain.cache["ARIBAUSERNAME"] = args.ariba_username
    if args.azure_storage_key:
        keychain.cache["AZURESTORAGEKEY"] = args.azure_storage_key
    if args.azure_storage_account:
        keychain.cache["STORAGEACCOUNTNAME"] = args.azure_storage_account
    if args.slack_key:
        keychain.cache["SLACKKEY"] = args.slack_key
    if not args.no_slack:
        slack = Slack(
            token=keychain.get_secret("SLACKKEY"),
            log_channel=keychain.get_config("log_channel"),
            update_channel=keychain.get_config("update_channel"),
        )
    else:
        # Create a fake slack object that just prints to the console
        slack = Slack(token="fake", log_channel="fake", update_channel="fake")
        slack.post_log = print
        slack.post_update = print

    slack.post_update(
        f"Scraper is starting to run! :rocket: You can follow updates on the #{slack.log_channel} channel."
    )
    # Get computer name and operating system
    slack.post_log(
        f"Running on {platform.node()} {platform.system_alias(platform.system(), platform.release(), platform.version())}"
    )
    slack.post_log("Checking if there is new data on the city website...")
    # Save open data with datestamp
    get_open_data().to_json(
        f'{OPEN_DATA_DIRECTORY}/open_data_{dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.json'
    )
    # Check if latest open data is the same as the next most recent
    open_data_files = sorted(Path(OPEN_DATA_DIRECTORY).glob("*.json"))
    skip_scraper = False
    if len(open_data_files) > 1:
        if filecmp.cmp(open_data_files[-1], open_data_files[-2]):
            skip_scraper = True
    if args.skip_scraper:
        skip_scraper = True
    if args.force:
        skip_scraper = False

    for file in OPEN_DATA_DIRECTORY.iterdir():
        df = pd.read_json(file)
        for k, v in df.iterrows():
            if v.CallNumber is None or not v.CallNumber == v.CallNumber:
                continue
            if "Doc" in str(v.CallNumber):
                call_path = ARIBA_DATA_DIRECTORY / v.CallNumber
            else:
                call_path = ARIBA_DATA_DIRECTORY / ("Doc" + v.CallNumber)
            call_path.mkdir(parents=True, exist_ok=True)
            v.to_json(call_path / file.name)

    # Now delete any duplicates
    hashes = set()
    for file in ARIBA_DATA_DIRECTORY.iterdir():
        if file.is_dir():
            for json_file in file.iterdir():
                if json_file.is_file():
                    try:
                        with open(json_file, "rb") as f:
                            file_hash = sha256(f.read()).hexdigest()
                        if file_hash in hashes:
                            json_file.unlink()
                        else:
                            hashes.add(file_hash)
                    except Exception as e:
                        continue

    if not skip_scraper:
        slack.post_log("It looks like there are new bids! Starting the scraper...")

        finished = False
        clicked = set()
        chrome_options = webdriver.ChromeOptions()
        # chrome_options.add_argument('--headless')
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        prefs = {"download.default_directory": str(DOWNLOAD_DIRECTORY)}
        chrome_options.add_experimental_option("prefs", prefs)
        driver = Ariba(
            service=ChromeService(ChromeDriverManager().install()),
            options=chrome_options,
            ariba_discovery_profile_key=keychain.get_config("aribaDiscoveryProfileKey"),
        )

        while not finished:
            try:
                finished = main_loop(
                    scraper_config=keychain,
                    closing_soon=not args.scrape_all,
                    download_everything=args.download_everything,
                )
            except Exception as e:
                slack.post_log(str(e))
                # Check if the issue is that we aren't logged in
                if not driver.is_logged_in():
                    driver.login(keychain)
                else:
                    driver.quit()
                    driver = Ariba(
                        service=ChromeService(ChromeDriverManager().install()),
                        options=chrome_options,
                        ariba_discovery_profile_key=keychain.get_config(
                            "aribaDiscoveryProfileKey"
                        ),
                    )

        slack.post_log("Ariba scraper is finished! :tada: Now performing cleanup...")

    # Move zips from download directory to repo's data directory
    for file in DOWNLOAD_DIRECTORY.iterdir():
        if file.suffix == ".zip":
            file.rename(ARIBA_DATA_DIRECTORY / file.name)

    extract_zip_and_move_html(ARIBA_DATA_DIRECTORY)
    move_pdfs(DOWNLOAD_DIRECTORY, ARIBA_DATA_DIRECTORY)

    parse_html(ARIBA_DATA_DIRECTORY).to_csv("metadata.csv", index=False)

    delete_duplicates(ARIBA_DATA_DIRECTORY)

    # drive = GoogleDrive(slack, keychain)
    # drive.upload_all_data(Path("data"))
    # Check if alternate JSON URL is provided in args
    # if args.json_url:
    #     scraper_config["json_url"] = args.json_url
    # if args.json_key:
    #     scraper_config["json_key"] = args.json_key

    response = transmit_json(
        keychain.get_config("json_url"), keychain.get_config("json_key"), slack
    )
    slack.post_log(f"Pushed JSON, received response: {response}\n{response.text}")

    finish_time = time()
    slack.post_update(f"Scraper is finished! :tada: :file_folder:")
    slack.post_log(f"Finished in {finish_time - start_time:.0f} seconds")
