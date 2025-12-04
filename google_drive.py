import io
import json
import os
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ['https://www.googleapis.com/auth/drive']

def get_drive_service():
    """Authenticates with Google Drive API using Application Default Credentials (ADC) and returns a service object."""
    creds, _ = google.auth.default(scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def list_files_in_folder(service, folder_id):
    """
    Lists all .pdf and .docx files in a Google Drive folder.
    Returns a list of dicts: {'id': str, 'name': str, 'link': str}
    """
    # Correction Bug : Exclusion explicite des fichiers générés (_processed)
    query = (
        f"'{folder_id}' in parents "
        f"and (mimeType='application/pdf' or mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document') "
        f"and not name contains '_processed'"
    )
    
    print(f"--- LOG DE DÉBOGAGE GOOGLE DRIVE ---")
    print(f"Requête API envoyée : q={query}")
    
    items = []
    page_token = None
    
    while True:
        results = service.files().list(
            q=query,
            pageSize=1000, # Maximize page size
            fields="nextPageToken, files(id, name, webViewLink)",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        
        items.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        
        if not page_token:
            break

    print(f"Nombre total de fichiers trouvés : {len(items)}")
    print(f"--- FIN DU LOG DE DÉBOGAGE ---")
    
    file_list = []
    for item in items:
        file_list.append({
            'id': item['id'],
            'name': item['name'],
            'link': item.get('webViewLink', '')
        })
        
    return file_list

def download_file(service, file_id, file_name, download_path):
    """Downloads a single file from Google Drive."""
    if not os.path.exists(download_path):
        os.makedirs(download_path)
        
    file_path = os.path.join(download_path, file_name)
    
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    
    done = False
    while not done:
        status, done = downloader.next_chunk()
        # print(f"Downloading {file_name}: {int(status.progress() * 100)}%")

    with open(file_path, 'wb') as f:
        f.write(fh.getvalue())
    # print(f"Downloaded '{file_name}' to '{file_path}'")
    
    return file_path

def upload_file_to_folder(service, file_path, folder_id):
    """Uploads a file to a specific Google Drive folder."""
    file_name = os.path.basename(file_path)
    media = MediaFileUpload(file_path, mimetype='application/octet-stream', resumable=True)
    
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink',
        supportsAllDrives=True
    ).execute()
    print(f"Uploaded '{file_name}' with ID: {file.get('id')}")
    return file.get('id'), file.get('webViewLink')

def get_or_create_folder(service, folder_name, parent_id=None):
    """Checks if a folder exists, creates it if not, and returns its ID."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    
    results = service.files().list(
        q=query,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        
        folder = service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute()
        return folder.get('id')

# --- SHEETS API ---

def get_sheets_service():
    """Authenticates with Google Sheets API."""
    creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets'])
    return build('sheets', 'v4', credentials=creds)

def fetch_pending_cvs(service, sheet_id, sheet_name="Feuille 1", target_status="EN_ATTENTE"):
    """
    Fetches rows where Status (Column E, index 4) matches target_status.
    Returns a list of dicts: {'row': int, 'file_id': str, 'file_name': str, 'json_link': str}
    """
    sheet_range = f"{sheet_name}!A:H"
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=sheet_id, range=sheet_range).execute()
    values = result.get('values', [])

    pending_cvs = []
    
    if not values:
        return []

    # Skip header
    for i, row in enumerate(values[1:], start=2): # Start at row 2 (1-based index for Sheets)
        # Row structure: [Date, Name, ID, Link, Status, JSON Link, PDF Link, Summary]
        # Check if row has enough columns and Status matches
        if len(row) > 4 and row[4] == target_status:
            pending_cvs.append({
                'row': i,
                'file_name': row[1] if len(row) > 1 else "Unknown",
                'file_id': row[2] if len(row) > 2 else "",
                'json_link': row[5] if len(row) > 5 else "", # Column F is JSON Link
                'pdf_link': row[6] if len(row) > 6 else ""   # Column G is PDF Link
            })
            
    return pending_cvs

def update_cv_status(service, sheet_id, row_number, status, sheet_name="Feuille 1", json_link="", pdf_link="", summary=""):
    """
    Updates the status, JSON link, PDF link, and summary for a specific row.
    Columns: E=Status, F=JSON Link, G=PDF Link, H=Summary
    """
    # We update range E{row}:H{row}
    range_name = f"{sheet_name}!E{row_number}:H{row_number}"
    
    values = [[status, json_link, pdf_link, summary]]
    body = {'values': values}
    
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=range_name,
        valueInputOption="USER_ENTERED", body=body
    ).execute()
    print(f"Updated Sheet Row {row_number}: {status}")

def reset_stuck_cvs(service, sheet_id, sheet_name="Feuille 1"):
    """
    Resets status to 'EN_ATTENTE' for rows where JSON Link is empty.
    This ensures that stuck or failed CVs are retried.
    """
    sheet_range = f"{sheet_name}!A:H"
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=sheet_id, range=sheet_range).execute()
    values = result.get('values', [])

    if not values:
        return

    # Skip header (start at index 1, row 2)
    for i, row in enumerate(values[1:], start=2):
        # Row structure: [Date, Name, ID, Link, Status, JSON Link, PDF Link, Summary]
        # Check if JSON Link (Column F, index 5) is empty
        json_link = row[5] if len(row) > 5 else ""
        status = row[4] if len(row) > 4 else ""
        
        if not json_link and status != "EN_ATTENTE":
            update_cv_status(service, sheet_id, i, "EN_ATTENTE", sheet_name=sheet_name)

import time
from googleapiclient.errors import HttpError

def append_to_sheet(service, sheet_id, values, sheet_name="Feuille 1", retries=10):
    """
    Appends a list of values as a new row to the specified Google Sheet.
    Includes exponential backoff for rate limiting (429 errors).
    """
    range_name = f"{sheet_name}!A:B" # Appending to columns A and B
    body = {'values': [values]}
    
    attempt = 0
    while attempt < retries:
        try:
            service.spreadsheets().values().append(
                spreadsheetId=sheet_id, range=range_name,
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            print(f"Appended to sheet: {values}")
            return
        except HttpError as error:
            if error.resp.status == 429:
                sleep_time = (2 ** attempt) + 1 # Exponential backoff + 1s buffer
                print(f"Quota exceeded (429) in append. Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                attempt += 1
            else:
                print(f"Error appending to sheet: {error}")
                raise
    
    print(f"Failed to append to sheet after {retries} retries.")
    raise Exception("Max retries exceeded for Google Sheets API write requests.")

def get_sheet_values(service, sheet_id, sheet_name="Feuille 1", value_render_option="FORMATTED_VALUE"):
    """
    Returns all values from the specified sheet.
    value_render_option: 'FORMATTED_VALUE' (default), 'UNFORMATTED_VALUE', or 'FORMULA'
    """
    range_name = f"{sheet_name}!A:C" # Columns A-C (Filename, Email, Phone)
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=range_name, valueRenderOption=value_render_option
    ).execute()
    return result.get('values', [])

def clear_and_write_sheet(service, sheet_id, values, sheet_name="Feuille 1", retries=10):
    """
    Clears the sheet and writes new values.
    Used for deduplication.
    """
    # 1. Clear (usually fast, but let's be safe)
    try:
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=f"{sheet_name}!A:Z"
        ).execute()
    except HttpError as error:
        print(f"Warning: Failed to clear sheet: {error}")

    # 2. Write with retry
    body = {'values': values}
    
    attempt = 0
    while attempt < retries:
        try:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"{sheet_name}!A1",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            print(f"Rewrote sheet with {len(values)} rows.")
            return
        except HttpError as error:
            if error.resp.status == 429:
                sleep_time = (2 ** attempt) + 1
                print(f"Quota exceeded (429) in rewrite. Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                attempt += 1
            else:
                raise
    raise Exception("Max retries exceeded for clear_and_write_sheet")

def format_header_row(service, sheet_id, sheet_name="Feuille 1"):
    """
    Formats the first row of the sheet to be bold.
    """
    # Get sheetId (integer) from sheetName (string)
    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    sheet_int_id = 0
    for s in sheets:
        if s.get("properties", {}).get("title") == sheet_name:
            sheet_int_id = s.get("properties", {}).get("sheetId")
            break
            
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_int_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "bold": True
                        }
                    }
                },
                "fields": "userEnteredFormat.textFormat.bold"
            }
        }
    ]
    
    body = {
        'requests': requests
    }
    
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body=body
    ).execute()
    print("Formatted header row as bold.")

def update_sheet_row(service, sheet_id, row_index, values, sheet_name="Feuille 1", retries=10):
    """
    Updates a specific row in the sheet.
    row_index is 0-based (but Sheets API uses 1-based for A1 notation).
    """
    sheet_row_num = row_index + 1
    range_name = f"{sheet_name}!A{sheet_row_num}:E{sheet_row_num}"
    
    body = {'values': [values]}
    
    attempt = 0
    while attempt < retries:
        try:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=range_name,
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            print(f"Updated row {sheet_row_num}: {values}")
            return
        except HttpError as error:
            if error.resp.status == 429:
                sleep_time = (2 ** attempt) + 1
                print(f"Quota exceeded (429) in update_row. Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                attempt += 1
            else:
                raise
    raise Exception("Max retries exceeded for update_sheet_row")
