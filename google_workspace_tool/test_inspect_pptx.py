import sys
import io
import zipfile
from googleapiclient.discovery import build
from google_workspace_tool.auth_utils import get_credentials
from google_workspace_tool.utils.download_utils import download_file

def test():
    creds = get_credentials(["https://www.googleapis.com/auth/drive.readonly"])
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    file_id = "1xUFuAnQfCIGTIupHfI0BC5029nm_42xL"
    print("Downloading...")
    raw_bytes = download_file(drive_service, file_id, supports_all_drives=True, timeout=60)
    
    zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    print("Zip contents:")
    for name in zf.namelist():
        if "comment" in name.lower() or "notes" in name.lower() or "slide" in name.lower():
            print(name)

if __name__ == '__main__':
    test()
