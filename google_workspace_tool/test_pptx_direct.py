import sys
import logging
from googleapiclient.discovery import build
from google_workspace_tool.auth_utils import get_credentials
from google_workspace_tool.utils.download_utils import download_file
from google_workspace_tool.readers.pptx_reader import _try_extract_pptx_text

logging.basicConfig(level=logging.INFO)

def test():
    creds = get_credentials(["https://www.googleapis.com/auth/drive.readonly"])
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    file_id = "17qrwrytVanT21eZy-vXgDapf-41BvCZ5"
    print("Downloading pptx_1...")
    # Use 60 seconds timeout to be safe
    raw_bytes = download_file(drive_service, file_id, supports_all_drives=True, timeout=60)
    print(f"Downloaded {len(raw_bytes)} bytes. Parsing...")
    
    text = _try_extract_pptx_text(raw_bytes)
    if text is None:
        print("Parsing returned None!")
    else:
        print(f"Parsed length: {len(text)}")
        print(text[:1000])

if __name__ == '__main__':
    test()
