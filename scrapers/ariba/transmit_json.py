import requests, json
import re
import pandas as pd
from pathlib import Path


def get_latest_open_data() -> pd.DataFrame:
    # Load most recent json file from open_data folder
    open_data_folder = Path.cwd() / "open_data"
    latest_json = sorted(open_data_folder.glob("*.json"))[-1]
    with open(latest_json, "r") as f:
        data = json.load(f)
    data = pd.DataFrame(data)
    # Drop letters from CallNumber column
    data["CallNumber"] = data["CallNumber"].str.replace("[A-Za-z]", "")
    return data


def get_list_of_documents(root: Path) -> list[str]:
    results = []
    for folder in root.iterdir():
        if folder.is_dir():
            results += get_list_of_documents(folder)
        else:
            results.append(folder.name)
    return results


def construct_file_metadata() -> pd.DataFrame:
    data_folder = Path.cwd() / "data"
    file_metadata = []
    for folder in data_folder.iterdir():
        if not folder.is_dir():
            continue
        temp = {
            "CallNumber": re.sub(r"[A-Za-z]", "", folder.name),
            "Documents": get_list_of_documents(folder),
        }
        file_metadata.append(temp)
    return pd.DataFrame(file_metadata)


def join_open_data_and_file_metadata() -> pd.DataFrame:
    open_data = get_latest_open_data()
    file_metadata = construct_file_metadata()
    # Join on CallNumber, dropping any rows that don't have a match
    return open_data.merge(file_metadata, on="CallNumber", how="inner")


def transmit_json(url: str) -> requests.Response:
    data = join_open_data_and_file_metadata()
    json_data = data.to_json(orient="records")
    response = requests.post(url, json=json_data)
    return response
