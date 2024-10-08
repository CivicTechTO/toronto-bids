import pandas as pd
from azure.storage.fileshare import ShareServiceClient

from secret_manager import Keychain


class AzureFileShare:
    def __init__(self, keychain: Keychain):
        connection_string = f"DefaultEndpointsProtocol=https;AccountName={keychain.get_config('storage_account_name')};AccountKey={keychain.get_secret('AZURESTORAGEKEY')};EndpointSuffix=core.windows.net"
        print(f"Connection string: {connection_string}")
        self.service = ShareServiceClient.from_connection_string(connection_string)

        self.share = self.service.get_share_client(keychain.get_config("share_name"))
        self.share_name = keychain.get_config("share_name")

    def list_files(self, directory: str) -> list[tuple[str, str]]:
        files = []
        dir_client = self.share.get_directory_client(directory.lstrip("/"))
        for item in dir_client.list_directories_and_files():
            if item.is_directory:
                files.extend(self.list_files(f"{directory}/{item.name}".lstrip("/")))
            else:
                files.append((item.name, f"{directory}/{item.name}".lstrip("/")))
        return files

    def list_files_handler(self, directory: str = "ariba_data") -> list[tuple[str, str]]:
        files = self.list_files(directory)
        return files

    def generate_download_link(self, file_path: str) -> str:
        return f"https://{self.service.account_name}.file.core.windows.net/{self.share_name}/{file_path}"

    def create_file_listing_dataframe(self) -> pd.DataFrame:
        files = self.list_files_handler()
        df = pd.DataFrame(files, columns=["File Name", "Location"])

        return df