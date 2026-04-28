import sys
import logging
from googleapiclient.discovery import build
from google_workspace_tool.auth_utils import get_credentials
from google_workspace_tool.drive_content_reader import read_drive_chunks

logging.basicConfig(level=logging.INFO)

def test():
    creds = get_credentials(["https://www.googleapis.com/auth/drive.readonly"])
    file_id = "17qrwrytVanT21eZy-vXgDapf-41BvCZ5"
    mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    
    print("Reading chunks...")
    chunks = read_drive_chunks(
        mime_type=mime_type,
        file_id=file_id,
        creds=creds,
        supports_all_drives=True,
    )
    
    for c in chunks:
        print(f"CHUNK ID: {c.chunk_id}")
        print(f"HEADING: {c.heading_path}")

if __name__ == "__main__":
    test()