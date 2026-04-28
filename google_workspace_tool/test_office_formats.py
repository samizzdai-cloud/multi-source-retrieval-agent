import sys
import logging
from googleapiclient.discovery import build
from google_workspace_tool.auth_utils import get_credentials

logging.basicConfig(level=logging.INFO)

def test():
    creds = get_credentials(["https://www.googleapis.com/auth/drive.readonly"])
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    # search for xlsx, pptx, txt
    q = "mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or mimeType='text/plain' or mimeType='application/vnd.openxmlformats-officedocument.presentationml.presentation'"
    response = drive_service.files().list(q=q, fields="files(id, name, mimeType)", pageSize=5).execute()
    files = response.get('files', [])
    
    for f in files:
        print(f"Found: {f['name']} ({f['mimeType']}) - ID: {f['id']}")
        if 'spreadsheet' in f['mimeType']:
            print("Testing Sheets API...")
            sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            try:
                sheets_service.spreadsheets().get(spreadsheetId=f['id']).execute()
                print("Sheets API WORKS for xlsx!")
            except Exception as e:
                print(f"Sheets API failed: {e}")
        elif 'presentation' in f['mimeType']:
            print("Testing Slides API...")
            slides_service = build("slides", "v1", credentials=creds, cache_discovery=False)
            try:
                slides_service.presentations().get(presentationId=f['id']).execute()
                print("Slides API WORKS for pptx!")
            except Exception as e:
                print(f"Slides API failed: {e}")

if __name__ == '__main__':
    test()
