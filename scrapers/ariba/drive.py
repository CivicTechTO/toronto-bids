from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import magic
from pathlib import Path
import pickle


class Drive:

    def __init__(self):
        scope = ['https://www.googleapis.com/auth/drive']

        creds = None
        # The file token.pickle stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if Path('token.pickle').exists():
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', scope)
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)
        # return Google Drive API service
        self.service = build('drive', 'v3', credentials=creds)

    def check_if_file_or_folder_exists(self, name, parent=None):
        query = f"name='{name}'"
        # if parent:
        #     query += f" and '{parent}' in parents"
        result = self.service.files().list(q=query).execute()
        if result['files']:
            return result['files'][0]['id']
        else:
            return None

    def create_folder(self, name, parent=None):
        if (file_id := self.check_if_file_or_folder_exists(name, parent)) is not None:
            print(f"Folder {name} already exists in Google Drive.")
            return file_id
        body = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent:
            body['parents'] = [parent]
        result = self.service.files().create(body=body).execute()
        # Return folder ID
        return result['id']

    def upload_file(self, file_path, folder_id=None):
        if (file_id := self.check_if_file_or_folder_exists(Path(file_path).name, folder_id)) is not None:
            print(f"File {file_path} already exists in Google Drive.")
            return file_id
        body = {
            'name': Path(file_path).name,
            'mimetype': magic.from_file(file_path, mime=True)
        }
        if folder_id is not None:
            body['parents'] = [folder_id]
        media = MediaFileUpload(file_path)
        print(f"Uploading {file_path} to Google Drive...")
        return self.service.files().create(body=body, media_body=media, fields='id').execute()

    def recursively_upload_directory(self, directory, folder_id=None):
        for path in Path(directory).iterdir():
            if path.is_dir():
                self.recursively_upload_directory(path, self.create_folder(path.name, folder_id))
            else:
                self.upload_file(path, folder_id)
#%%
if __name__ == '__main__':
    drive = Drive()
    drive.recursively_upload_directory('data', drive.create_folder('data'))