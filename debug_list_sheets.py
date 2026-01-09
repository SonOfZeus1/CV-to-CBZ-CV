
import os
import json
from dotenv import load_dotenv
from google_drive import get_sheets_service

def verify_sheet_titles():
    load_dotenv()
    sheet_id = os.getenv('EMAIL_SHEET_ID')
    
    if not sheet_id:
        print("ERROR: Missing EMAIL_SHEET_ID")
        return

    try:
        service = get_sheets_service()
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        
        print("\n--- Available Sheets ---")
        for sheet in spreadsheet.get('sheets', []):
            title = sheet['properties']['title']
            idx = sheet['properties']['sheetId']
            print(f"Name: '{title}' (ID: {idx})")
            
        print("------------------------\n")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify_sheet_titles()
