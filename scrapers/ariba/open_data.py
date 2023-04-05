import requests
import xml.etree.ElementTree as ET
import pandas as pd


def get_xml(url: str) -> pd.DataFrame:
    response = requests.get(url).content
    root = ET.fromstring(response)
    return parse_xml(root)


def parse_xml(root: ET.Element) -> pd.DataFrame:
    data = []
    for child in root:
        entry_dict = {}
        for entry in child:
            if entry[0] is None or (
                hasattr(entry[0], "text") and entry[0].text is None
            ):
                entry_dict[entry.get("name")] = None
            elif isinstance(entry[0], ET.Element):
                entry_dict[entry.get("name")] = entry[0].text.strip()
            else:
                entry_dict[entry.get("name")] = str(entry[0]).strip()
        data.append(entry_dict)
    return pd.DataFrame(data)


def get_open_data() -> pd.DataFrame:
    od = OpenData()
    response = od.get_packages()
    xml = get_xml(response["result"]["resources"][0]["url"])
    return xml


class OpenData:
    def __init__(self):
        self.base_url = "https://ckan0.cf.opendata.inter.prod-toronto.ca/"
        self.api_url = self.base_url + "api/3/action/package_show"
        self.params = {"id": "call-documents-for-the-purchase-of-goods-and-services"}

    def get_packages(self) -> dict:
        response = requests.get(self.api_url, params=self.params).json()
        for idx, resource in enumerate(response["result"]["resources"]):
            if not resource["datastore_active"]:
                for k, v in self.get_metadata(resource["id"]).items():
                    if k not in resource.keys():
                        resource[k] = v
        return response

    def get_metadata(self, resource_id: str) -> dict:
        url = self.base_url + "api/3/action/resource_show?id=" + resource_id
        response = requests.get(url).json()
        return response["result"]


# %%
