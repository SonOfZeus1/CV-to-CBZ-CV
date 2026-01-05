import os
from dotenv import load_dotenv
from google_drive import get_drive_service

load_dotenv()

def list_folders():
    try:
        service = get_drive_service()
        results = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)",
            pageSize=50
        ).execute()
        folders = results.get('files', [])
        
        print("--- Folders Found ---")
        for f in folders:
            print(f"Name: {f['name']}, ID: {f['id']}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_folders()
