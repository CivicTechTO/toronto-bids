from typing import Any
from hashlib import sha256
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import base64

from slack import Slack

try:
    import magic
except ImportError:
    # If python-magic is not installed, use a dummy class because Google Drive will attempt to infer the mime type anyway
    class magic:
        def from_file(path: str, mime: bool = False) -> str:
            return None


from pathlib import Path
import pickle, time, socket, os, json


class File:
    """
    A file or folder on Google Drive
    """

    def __init__(self, path: Path, docid: str, parent: "Folder" = None):
        self.path = path
        self.name = Path(path).name
        self.docid = docid  # This is OUR docid, not Google's
        self.mime_type = (
            self.get_mime_type()
        )  # If we can tell Google what the mime type is, we should
        assert (
            isinstance(parent, Folder) or parent is None
        ), "Parent must be a Folder or None"
        self.parent = parent
        self.sha256 = self.get_sha256()

    def get_mime_type(self) -> str:
        return magic.from_file(self.path, mime=True)

    def is_uploaded(self, drive: "GoogleDrive") -> tuple[bool, Any]:
        """
        Check if this file is already uploaded to Google Drive
        :param drive: the uploader object
        :return: whether the file is uploaded, and any metadata about the file
        """

        # Construct a query to search for the file
        query = f"name = '{self.name}'"
        if self.parent:
            query += f" and '{self.parent.get_file_id(drive)}' in parents"
        try:
            result = drive.service.files().list(q=query, fields="*").execute()
        except HttpError as e:
            if e.resp.status == 404:
                return False, None
            drive.slack.post_log(f"Error: {e}")
            return False, None
        if result["files"]:
            return True, result["files"][0]
        return False, None

    def is_dir(self):
        return False

    def get_sha256(self) -> str:
        return sha256(self.path.read_bytes()).hexdigest()

    def __repr__(self):
        if self.parent:
            return f"{self.parent}/{self.name}"
        return self.name

    def __str__(self):
        return self.__repr__()


class Folder(File):
    """
    A folder on Google Drive
    """

    def __init__(self, path: Path, docid: str, parent: "Folder" = None):
        super().__init__(path, docid, parent)
        self.mime_type = self.get_mime_type()

    def get_mime_type(self) -> str:
        # Google Drive folders have a special mime type
        return "application/vnd.google-apps.folder"

    def is_dir(self):
        return True

    def iterdir(self):
        # Yield all files in this folder, constructing new File/Folder objects as needed
        for path in self.path.iterdir():
            if path.is_dir():
                yield Folder(path, self.docid, self)
            else:
                yield File(path, self.docid, self)

    def get_file_id(self, drive: "GoogleDrive") -> str:
        """
        Get the Google Drive file ID for this folder
        :param drive:
        :return: Google Drive file ID, required for uploading files to this folder
        """
        is_uploaded, file_metadata = self.is_uploaded(drive)
        if is_uploaded:
            return file_metadata["id"]
        return None

    def get_sha256(self) -> str:
        return None


class GoogleDrive:
    def __init__(self, slack: Slack):
        # Loads credentials and creates Google Drive API service

        scope = ["https://www.googleapis.com/auth/drive"]

        creds = None
        # The file token.pickle stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        token_json = os.environ.get("GDRIVE_TOKEN")
        creds = json.loads(base64.b64decode(token_json.encode()).decode())
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds["valid"]:
            if creds and creds["expired"] and creds["refresh_token"]:
                creds.refresh(Request())
            else:
                credentials_json = os.environ.get("GDRIVE_CREDENTIALS")
                creds = json.loads(base64.b64decode(credentials_json.encode()).decode())
                flow = InstalledAppFlow.from_client_config(creds, scope)
                creds = flow.run_local_server(port=0)
            # Save the credentials to the environment file
            os.environ["GDRIVE_TOKEN"] = creds.to_json()

        # return Google Drive API service
        self.service = build("drive", "v3", credentials=creds)
        self.slack = slack

    def upload_file(self, file: File) -> None:
        is_uploaded, file_metadata = file.is_uploaded(self)
        if is_uploaded:
            # Same filename exists but we need to compare hashes
            if file.sha256 == file_metadata["sha256Checksum"]:
                return
            else:
                self.slack.post_log(
                    f"File {file.name} already exists but has changed. Modifying name then uploading..."
                )
                file.name = f"{file.name} ({time.time()})"
        file_metadata = {
            "name": file.name,
            "appProperties": {
                "docid": file.docid,
                "timestamp": str(time.time()),
                "hostname": socket.gethostname(),
            },
        }
        if file.parent:
            # If the file has a parent, we need to upload it to that folder. Requires getting the folder's file ID
            parent_uploaded, parent_metadata = file.parent.is_uploaded(self)
            if not parent_uploaded:
                # Shouldn't be able to reach this point as the folder should have been uploaded first.
                raise FileNotFoundError(
                    "Attempting to upload file to non-existent folder."
                )
            file_metadata["parents"] = [parent_metadata["id"]]
        media = MediaFileUpload(file.path, mimetype=file.mime_type)
        self.slack.post_log(f"Uploading {file.name}...")
        self.service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()

    def upload_folder(self, folder: Folder) -> None:
        # Very similar to the upload_file method, but we don't need to upload the file contents

        is_uploaded, _ = folder.is_uploaded(self)
        if is_uploaded:
            return
        folder_metadata = {
            "name": folder.name,
            "mimeType": folder.mime_type,
            "appProperties": {
                "docid": folder.docid,
                "timestamp": str(time.time()),
                "hostname": socket.gethostname(),
            },
        }
        if folder.parent:
            parent_uploaded, parent_metadata = folder.parent.is_uploaded(self)
            if not parent_uploaded:
                raise FileNotFoundError(
                    "Attempting to create folder within a non-existent folder."
                )
            folder_metadata["parents"] = [parent_metadata["id"]]
        self.slack.post_log(f"Creating {folder.name}...")
        result = (
            self.service.files().create(body=folder_metadata, fields="id").execute()
        )

    def upload_directory(self, folder: Folder):
        # Recursively upload all files and folders in a directory.

        self.upload_folder(folder)
        for file in folder.iterdir():
            if file.is_dir():
                self.upload_directory(file)
            else:
                self.upload_file(file)

    def upload_all_data(self, root: Path):
        # Special method to handle the root directory. Constructs the Folder objects and then calls upload_directory.
        root_folder = Folder(root, "root")
        root_is_uploaded, root_metadata = root_folder.is_uploaded(self)
        if not root_is_uploaded:
            self.upload_folder(root_folder)

        for path in root.iterdir():
            if not path.is_dir():
                continue
            if not path.name.startswith("Doc"):
                continue
            folder = Folder(path, path.name, root_folder)
            self.upload_directory(folder)


if __name__ == "__main__":
    drive = GoogleDrive()
    drive.upload_all_data(Path("data"))
# %%
