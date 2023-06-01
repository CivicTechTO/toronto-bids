import requests, json
import re
import pandas as pd
from azurefileshare import AzureFileShare
from secret_manager import Keychain
from pathlib import Path


def get_latest_open_data() -> pd.DataFrame:
    # Load most recent json file from open_data folder
    open_data_folder = Path.cwd() / "files" / "open_data"
    latest_json = sorted(open_data_folder.glob("*.json"))[-1]
    with open(latest_json, "r") as f:
        data = json.load(f)
    data = pd.DataFrame(data)
    # Drop letters from CallNumber column
    data["CallNumber"] = data["CallNumber"].str.replace("[A-Za-z]", "", regex=True)
    # Rename the column "$4" to "ShowDateClosing"
    data.rename(columns={"$4": "ShowDateClosing"}, inplace=True)
    # Drop $8 and $12
    data.drop(columns=["$8", "$12"], inplace=True)
    # Parse the date columns (format is e.g. June 12, 2023)
    try:
        data["ShowDateClosing"] = pd.to_datetime(
            data["ShowDateClosing"], format="%B %d, %Y"
        )
        data["ShowDatePosted"] = pd.to_datetime(
            data["ShowDatePosted"], format="%B %d, %Y"
        )
    except ValueError:
        # If that format failed, it's going to be like [<font color=red><b>May 30, 2023 12:00 noon</b>]
        data["ShowDateClosing"] = pd.to_datetime(
            data["ShowDateClosing"].str.extract(r"(\w+ \d+, \d+)", expand=False),
            format="%B %d, %Y",
        )
        data["ShowDatePosted"] = pd.to_datetime(
            data["ShowDatePosted"].str.extract(r"(\w+ \d+, \d+)", expand=False),
            format="%B %d, %Y",
        )
    # Now convert the date columns to strings of format YYYY-MM-DD
    data["ShowDateClosing"] = data["ShowDateClosing"].dt.strftime("%Y-%m-%d")
    data["ShowDatePosted"] = data["ShowDatePosted"].dt.strftime("%Y-%m-%d")
    return data


def join_open_data_and_file_metadata() -> pd.DataFrame:
    afs = AzureFileShare(Keychain())
    open_data = get_latest_open_data()
    file_metadata = afs.create_file_listing_dataframe()
    file_metadata["CallNumber"] = None
    for k, v in file_metadata.iterrows():
        if "Doc" not in v.Location:
            continue
        location = v.Location.split("/")
        call = [l for l in location if "Doc" in l][0]
        call = re.sub("[A-Za-z]", "", call)
        file_metadata.loc[k, "CallNumber"] = call

    grouped_file_metadata = {}
    for k, v in file_metadata.iterrows():
        if v["CallNumber"] not in grouped_file_metadata.keys():
            grouped_file_metadata[v["CallNumber"]] = {
                "File Name": [],
                "Location": [],
                "Download Link": [],
            }
        grouped_file_metadata[v["CallNumber"]]["File Name"].append(v["File Name"])
        grouped_file_metadata[v["CallNumber"]]["Location"].append(v["Location"])
        grouped_file_metadata[v["CallNumber"]]["Download Link"].append(
            v["Download Link"]
        )

    grouped_file_metadata = pd.DataFrame(grouped_file_metadata).transpose()
    grouped_file_metadata.index.name = "CallNumber"
    grouped_file_metadata.reset_index(inplace=True)
    return open_data.merge(
        grouped_file_metadata, on="CallNumber", how="inner", suffixes=("", "_y")
    )


def transmit_json(url: str, password: str) -> requests.Response:
    data = join_open_data_and_file_metadata()
    json_data = {"list": data.to_dict(orient="records")}
    # Post data to url, with basic password authentication
    print(f"Attempting to post {len(data)} records to {url}")
    # before sending, preview the request
    print(requests.Request("POST", url, json=json_data).prepare().body)

    response = requests.post(url, json=json_data)  # , auth=("user", password))
    return response


# %%
