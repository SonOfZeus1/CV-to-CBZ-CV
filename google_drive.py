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
        f"and not name contains '_processed' "
        f"and trashed=false"
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

def move_file(service, file_id, current_folder_id, new_folder_id):
    """
    Moves a file from one folder to another.
    """
    try:
        # Retrieve the existing parents to remove
        file = service.files().get(fileId=file_id, fields='parents').execute()
        previous_parents = ",".join(file.get('parents'))
        
        # Move the file by adding the new parent and removing the old one
        service.files().update(
            fileId=file_id,
            addParents=new_folder_id,
            removeParents=previous_parents,
            fields='id, parents',
            supportsAllDrives=True
        ).execute()
        # print(f"Moved file {file_id} to folder {new_folder_id}")
    except HttpError as error:
        if error.resp.status == 404:
            print(f"Warning: File {file_id} not found during move (likely already moved).")
        else:
            print(f"Error moving file {file_id}: {error}")
    except Exception as e:
        print(f"Error moving file {file_id}: {e}")


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
    sheet_range = f"'{sheet_name}'!A:H"
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
    range_name = f"'{sheet_name}'!E{row_number}:H{row_number}"
    
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
    sheet_range = f"'{sheet_name}'!A:H"
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
    range_name = f"'{sheet_name}'!A:B" # Appending to columns A and B
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
    range_name = f"'{sheet_name}'!A:E" # Columns A-E (Filename, Email, Phone, Status, JSON Link)
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
            spreadsheetId=sheet_id, range=f"'{sheet_name}'!A:Z"
        ).execute()
    except HttpError as error:
        print(f"Warning: Failed to clear sheet: {error}")

    # 2. Write with retry
    body = {'values': values}
    
    attempt = 0
    while attempt < retries:
        try:
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
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
    # Determine range based on length of values
    # A=1, B=2, C=3, D=4, E=5
    end_col_char = chr(ord('A') + len(values) - 1)
    range_name = f"'{sheet_name}'!A{sheet_row_num}:{end_col_char}{sheet_row_num}"
    
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

import re

def fetch_actionable_cvs(service, sheet_id, sheet_name="Feuille 1", target_status="A TRAITER"):
    """
    Fetches rows where Status (Column D, index 3) matches target_status.
    Extracts File ID from the formula in Column A.
    Returns a list of dicts: {'row': int, 'file_id': str, 'file_name': str}
    """
    # Use FORMULA render option to get the hyperlink formula
    rows = get_sheet_values(service, sheet_id, sheet_name, value_render_option='FORMULA')
    
    actionable_cvs = []
    
    if not rows:
        return []

    # Skip header
    for i, row in enumerate(rows[1:], start=2): # Start at row 2
        # Row: [Filename, Email, Phone, Status, JSON Link]
        if len(row) > 3:
            status = row[3].strip().upper()
            if status == target_status:
                raw_filename = row[0]
                
                # Extract File ID from HYPERLINK formula
                # Format: =HYPERLINK("https://drive.google.com/file/d/FILE_ID/view...", "name")
                # Regex for ID: /d/([a-zA-Z0-9_-]+)
                file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', raw_filename)
                
                # Extract clean name
                name_match = re.search(r'"([^"]+)"\)$', raw_filename)
                clean_name = name_match.group(1) if name_match else "Unknown"
                
                if file_id_match:
                    file_id = file_id_match.group(1)
                    actionable_cvs.append({
                        'row': i,
                        'file_id': file_id,
                        'file_name': clean_name
                    })
                else:
                    print(f"Warning: Could not extract File ID from row {i}: {raw_filename}")
                    
    return actionable_cvs

def set_column_validation(service, sheet_id, sheet_name, col_index, options):
    """
    Sets data validation (dropdown) for a specific column.
    col_index: 0-based index (e.g., 3 for Column D).
    options: List of strings for the dropdown.
    """
    # Get sheetId
    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    sheet_int_id = 0
    for s in sheets:
        if s.get("properties", {}).get("title") == sheet_name:
            sheet_int_id = s.get("properties", {}).get("sheetId")
            break
            
    # Define range (Skip header row 0, go to end of sheet)
    # col_index 3 = Column D
    
    requests = [
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_int_id,
                    "startRowIndex": 1, # Skip header
                    "startColumnIndex": col_index,
                    "endColumnIndex": col_index + 1
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": opt} for opt in options]
                    },
                    "showCustomUi": True,
                    "strict": False # Allow other values like "EXTRACTION_EN_COURS"
                }
            }
        }
    ]
    
    body = {
        'requests': requests
    }
    
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body=body
        ).execute()
        print(f"Set validation for column index {col_index} with options {options}")
    except HttpError as error:
        print(f"Warning: Failed to set validation: {error}")

def append_batch_to_sheet(service, sheet_id, rows, sheet_name="Feuille 1", retries=10):
    """
    Appends multiple rows to the sheet in one API call.
    rows: List of lists (e.g., [[val1, val2], [val3, val4]])
    """
    if not rows:
        return

    range_name = f"'{sheet_name}'!A:F"
    body = {'values': rows}
    
    attempt = 0
    while attempt < retries:
        try:
            service.spreadsheets().values().append(
                spreadsheetId=sheet_id, range=range_name,
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            print(f"Appended {len(rows)} rows to sheet.")
            return
        except HttpError as error:
            if error.resp.status == 429:
                sleep_time = (2 ** attempt) + 1
                print(f"Quota exceeded (429) in batch append. Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                attempt += 1
            else:
                print(f"Error appending batch: {error}")
                raise
    
    raise Exception("Max retries exceeded for append_batch_to_sheet")

def batch_update_rows(service, sheet_id, updates, sheet_name="Feuille 1", retries=10):
    """
    Updates multiple rows in one batchUpdate call.
    updates: List of tuples (row_index_0_based, values_list)
    """
    if not updates:
        return

    # Get sheetId
    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    sheet_int_id = 0
    for s in sheets:
        if s.get("properties", {}).get("title") == sheet_name:
            sheet_int_id = s.get("properties", {}).get("sheetId")
            break
            
    requests = []
    for row_idx, values in updates:
        # Create a PasteDataRequest or UpdateCellsRequest?
        # UpdateCells is better for specific ranges, but requires constructing RowData.
        # easier to use value ranges with batchUpdate? No, values.batchUpdate exists!
        pass
    
    # Actually, spreadsheets.values.batchUpdate is easier for multiple ranges!
    # But wait, values.batchUpdate takes a list of ValueRanges.
    # Each ValueRange has a range and values.
    
    data = []
    for row_idx, values in updates:
        sheet_row_num = row_idx + 1
        end_col_char = chr(ord('A') + len(values) - 1)
        range_name = f"'{sheet_name}'!A{sheet_row_num}:{end_col_char}{sheet_row_num}"
        data.append({
            'range': range_name,
            'values': [values]
        })
        
    body = {
        'valueInputOption': 'USER_ENTERED',
        'data': data
    }
    
    attempt = 0
    while attempt < retries:
        try:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id,
                body=body
            ).execute()
            print(f"Batch updated {len(updates)} rows.")
            return
        except HttpError as error:
            if error.resp.status == 429:
                sleep_time = (2 ** attempt) + 1
                print(f"Quota exceeded (429) in batch update. Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)
                attempt += 1
            else:
                print(f"Error batch updating: {error}")
                raise
    
    raise Exception("Max retries exceeded for batch_update_rows")

