import sys
import logging
import io
import time
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google_workspace_tool.auth_utils import get_credentials

logging.basicConfig(level=logging.INFO)

def upload_and_test():
    creds = get_credentials(["https://www.googleapis.com/auth/drive"])
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    slides_service = build("slides", "v1", credentials=creds, cache_discovery=False)
    
    # 1. Test CSV
    csv_content = b"name,age\nAlice,30\nBob,25"
    media = MediaIoBaseUpload(io.BytesIO(csv_content), mimetype='text/csv')
    csv_file = drive_service.files().create(body={'name': 'test.csv'}, media_body=media).execute()
    print(f"Created CSV: {csv_file['id']}")
    
    # Try reading with Sheets API
    try:
        res = sheets_service.spreadsheets().get(spreadsheetId=csv_file['id']).execute()
        print("Sheets API works for CSV!")
    except Exception as e:
        print(f"Sheets API failed for CSV: {e}")
        
    # 2. Test TXT
    txt_content = b"Hello world, this is a test text file."
    media = MediaIoBaseUpload(io.BytesIO(txt_content), mimetype='text/plain')
    txt_file = drive_service.files().create(body={'name': 'test.txt'}, media_body=media).execute()
    print(f"Created TXT: {txt_file['id']}")
    
    try:
        res = drive_service.files().get_media(fileId=txt_file['id']).execute()
        print(f"Drive API get_media works for TXT! Content: {res}")
    except Exception as e:
        print(f"Drive API get_media failed for TXT: {e}")

    # cleanup
    drive_service.files().delete(fileId=csv_file['id']).execute()
    drive_service.files().delete(fileId=txt_file['id']).execute()

if __name__ == '__main__':
    upload_and_test()
