import time
import random
import functools
import io
import json
import os
import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
import ssl
import socket
import httplib2
from google.auth.exceptions import TransportError

SCOPES = ['https://www.googleapis.com/auth/drive']

def execute_with_retry(func, retries=10, delay_base=2):
    """
    Executes a function (usually an API call) with exponential backoff retry logic.
    Handles SSL errors, socket timeouts, and rate limits (429).
    """
    attempt = 0
    while attempt < retries:
        try:
            return func()
        except (ssl.SSLEOFError, socket.timeout, socket.error, httplib2.HttpLib2Error, TransportError) as e:
            sleep_time = (delay_base ** attempt) + random.uniform(0, 1)
            print(f"Network error ({type(e).__name__}): {e}. Retrying in {sleep_time:.2f}s...")
            time.sleep(sleep_time)
            attempt += 1
        except HttpError as error:
            if error.resp.status in [429, 500, 502, 503, 504]:
                sleep_time = (delay_base ** attempt) + random.uniform(0, 1)
                print(f"API Error ({error.resp.status}): {error}. Retrying in {sleep_time:.2f}s...")
                time.sleep(sleep_time)
                attempt += 1
            else:
                raise
        except Exception as e:
            # For other unexpected errors, we might want to retry or raise.
            # Given the flakiness, let's retry on generic Exception if it looks network-y, 
            # but usually it's safer to raise unless we know it's safe.
            # However, the previous code retried on Exception.
            print(f"Unexpected error: {e}. Retrying in {(delay_base ** attempt):.2f}s...")
            time.sleep((delay_base ** attempt) + 1)
            attempt += 1
            
    raise Exception(f"Max retries ({retries}) exceeded for operation.")

def get_drive_service():
    """Authenticates with Google Drive API using Application Default Credentials (ADC) and returns a service object."""
    creds, _ = google.auth.default(scopes=SCOPES)
    return build('drive', 'v3', credentials=creds)

def list_files_in_folder(service, folder_id, order_by=None, page_size=1000, mime_types=None):
    """
    Lists files in a specific Google Drive folder.
    Returns a list of file metadata (id, name, webViewLink, modifiedTime).
    """
    # Default to PDF and DOCX if no mime_types provided (Backward Compatibility)
    if mime_types is None:
        mime_types = [
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ]
        
    # Build query dynamically
    mime_query_parts = []
    for mime in mime_types:
        if mime == 'text/markdown':
            # Special case for markdown which might be identified by extension
            mime_query_parts.append("(mimeType = 'text/markdown' or name contains '.md')")
        else:
            mime_query_parts.append(f"mimeType = '{mime}'")
            
    mime_query = " or ".join(mime_query_parts)
    
    query = (
        f"'{folder_id}' in parents "
        f"and ({mime_query}) "
        f"and trashed = false"
    )
    files = []
    page_token = None
    
    while True:
        results = execute_with_retry(lambda: service.files().list(
            q=query,
            pageSize=page_size,
            orderBy=order_by,
            fields="nextPageToken, files(id, name, webViewLink, modifiedTime, parents)",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute())

        files.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        
        # If we have a limit (page_size < 1000) and we reached it, stop.
        # Note: pageSize in API is per page. If we want a hard limit on total files, 
        # we should check len(files). But for now, let's assume page_size is the batch we want 
        # if we are doing one page. 
        # Actually, if order_by is set, we usually want just the top N.
        # So if we have enough files, break.
        if len(files) >= page_size:
            files = files[:page_size]
            break
            
        if not page_token:
            break
    # Transform to expected format
    file_list = []
    for item in files:
        file_list.append({
            'id': item['id'],
            'name': item['name'],
            'link': item.get('webViewLink', ''),
            'modifiedTime': item.get('modifiedTime', '')
        })
        
    return file_list

def download_file(service, file_id, file_name, download_path):
    """Downloads a single file from Google Drive."""
    if not os.path.exists(download_path):
        os.makedirs(download_path, exist_ok=True)
        
    file_path = os.path.join(download_path, file_name)
    
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    
    done = False
    while not done:
        try:
            status, done = execute_with_retry(lambda: downloader.next_chunk())
            # print(f"Downloading {file_name}: {int(status.progress() * 100)}%")
        except Exception as e:
            print(f"Error downloading {file_name}: {e}")
            raise

    with open(file_path, 'wb') as f:
        f.write(fh.getvalue())
    # print(f"Downloaded '{file_name}' to '{file_path}'")
    
    return file_path

def upload_file_to_folder(service, file_path, folder_id, mime_type=None):
    """
    Uploads a file to a specific Google Drive folder.
    If a file with the same name exists, it OVERWRITES it.
    """
    file_name = os.path.basename(file_path)
    
    if not mime_type:
        mime_type = 'application/octet-stream'
        
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
    
    # 1. Check if file exists
    query = f"name = '{file_name}' and '{folder_id}' in parents and trashed = false"
    existing_file_id = None
    
    try:
        results = execute_with_retry(lambda: service.files().list(
            q=query, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True
        ).execute())
        files = results.get('files', [])
        if files:
            existing_file_id = files[0]['id']
            print(f"File '{file_name}' already exists (ID: {existing_file_id}). Overwriting...")
    except Exception as e:
        print(f"Warning: Failed to check for existing file: {e}")

    try:
        if existing_file_id:
            # 2. Update existing file
            file = execute_with_retry(lambda: service.files().update(
                fileId=existing_file_id,
                media_body=media,
                fields='id, webViewLink',
                supportsAllDrives=True
            ).execute())
            print(f"Overwritten '{file_name}' with ID: {file.get('id')}")
        else:
            # 3. Create new file
            file_metadata = {
                'name': file_name,
                'parents': [folder_id]
            }
            file = execute_with_retry(lambda: service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink',
                supportsAllDrives=True
            ).execute())
            print(f"Uploaded '{file_name}' with ID: {file.get('id')}")
            
        return file.get('id'), file.get('webViewLink')
    except Exception as e:
        print(f"Error uploading {file_name}: {e}")
        raise

def get_or_create_folder(service, folder_name, parent_id=None):
    """Checks if a folder exists, creates it if not, and returns its ID."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder'"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    
    try:
        results = execute_with_retry(lambda: service.files().list(
            q=query,
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute())
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
            
            folder = execute_with_retry(lambda: service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute())
            return folder.get('id')
    except Exception as e:
        print(f"Error in get_or_create_folder: {e}")
        raise

def move_file(service, file_id, current_folder_id, new_folder_id):
    """
    Moves a file from one folder to another.
    """
    try:
        # Retrieve the existing parents to remove
        file = execute_with_retry(lambda: service.files().get(fileId=file_id, fields='parents', supportsAllDrives=True).execute())
        current_parents_list = file.get('parents', [])
        
        # Check if already in new_folder_id
        if new_folder_id in current_parents_list:
            print(f"File {file_id} is already in folder {new_folder_id}. Skipping move.")
            return

        previous_parents = ",".join(current_parents_list)
        
        # Move the file by adding the new parent
        try:
            execute_with_retry(lambda: service.files().update(
                fileId=file_id,
                addParents=new_folder_id,
                removeParents=previous_parents,
                fields='id, parents',
                supportsAllDrives=True
            ).execute())
            # print(f"Moved file {file_id} to folder {new_folder_id}")
            return
        except HttpError as error:
            if error.resp.status == 404:
                print(f"Warning: File {file_id} not found during move (likely already moved).")
                return
            else:
                raise
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
    
    try:
        execute_with_retry(lambda: service.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body=body
        ).execute())
        print(f"Appended to sheet: {values}")
    except Exception as e:
        print(f"Error appending to sheet: {e}")
        raise

def get_sheet_values(service, sheet_id, sheet_name="Feuille 1", value_render_option="FORMATTED_VALUE"):
    """
    Returns all values from the specified sheet.
    value_render_option: 'FORMATTED_VALUE' (default), 'UNFORMATTED_VALUE', or 'FORMULA'
    """
    range_name = f"'{sheet_name}'!A:Z" # Read all columns to support expansion
    
    try:
        result = execute_with_retry(lambda: service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=range_name, valueRenderOption=value_render_option
        ).execute())
        return result.get('values', [])
    except Exception as e:
        print(f"Error in get_sheet_values: {e}")
        raise

def clear_and_write_sheet(service, sheet_id, values, sheet_name="Feuille 1", retries=10):
    """
    Clears the sheet and writes new values.
    Used for deduplication.
    """
    # 1. Clear (usually fast, but let's be safe)
    # 1. Clear (usually fast, but let's be safe)
    try:
        execute_with_retry(lambda: service.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=f"'{sheet_name}'!A:Z"
        ).execute())
    except HttpError as error:
        print(f"Warning: Failed to clear sheet: {error}")

    # 2. Write with retry
    body = {'values': values}
    
    try:
        execute_with_retry(lambda: service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
            valueInputOption="USER_ENTERED", body=body
        ).execute())
        print(f"Rewrote sheet with {len(values)} rows.")
    except Exception as e:
        print(f"Error in clear_and_write_sheet: {e}")
        raise

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
    
    body = {'values': [values]}
    
    try:
        execute_with_retry(lambda: service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body=body
        ).execute())
        print(f"Updated row {sheet_row_num}: {values}")
    except Exception as e:
        print(f"Error in update_sheet_row: {e}")
        raise

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
    
    # Retry logic for set_column_validation
    try:
        execute_with_retry(lambda: service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body=body
        ).execute())
        print(f"Set validation for column index {col_index} with options {options}")
    except Exception as e:
        print(f"Warning: Failed to set validation: {e}")

def upsert_batch_to_sheet(service, sheet_id, rows, sheet_name="Candidats", email_col_index=2):
    """
    Updates existing rows if Email matches, otherwise appends new rows.
    email_col_index: 0-based index of the Email column (Default 2 for 'Candidats' sheet: Name, Surname, Email...)
    """
    if not rows:
        return

    # 1. Read existing data to find duplicates
    existing_values = get_sheet_values(service, sheet_id, sheet_name)
    
    # Map Email -> Row Index (0-based relative to sheet)
    email_map = {}
    if existing_values:
        for i, row in enumerate(existing_values):
            if len(row) > email_col_index:
                email = row[email_col_index].strip().lower()
                if email:
                    email_map[email] = i # i is 0-based index of the row

    rows_to_append = []
    updates = []

    for new_row in rows:
        # Check if email exists in new_row
        if len(new_row) > email_col_index:
            email = str(new_row[email_col_index]).strip().lower()
            
            if email and email in email_map:
                # Update existing row
                row_index = email_map[email]
                # Convert row_index to A1 notation (1-based)
                # A=1, B=2...
                # We assume we update the whole row from A to end of new_row length
                end_col_char = chr(ord('A') + len(new_row) - 1)
                range_name = f"'{sheet_name}'!A{row_index + 1}:{end_col_char}{row_index + 1}"
                
                updates.append({
                    'range': range_name,
                    'values': [new_row]
                })
                # Debug: Print first update details
                if len(updates) == 1:
                    print(f"DEBUG: Updating row {row_index+1}. Row Len: {len(new_row)}. Range: {range_name}.")
                    print(f"DEBUG: Last Val (JSON Link): '{new_row[-1]}'")
            else:
                # New row
                rows_to_append.append(new_row)
        else:
            # No email, treat as new? Or skip? Let's append.
            rows_to_append.append(new_row)

    # 2. Prepare Updates (Existing + New)
    # Calculate next available row for appends
    next_row = len(existing_values) + 1
    
    for new_row in rows_to_append:
        # Calculate range for new row (A{next_row}:...)
        end_col_char = chr(ord('A') + len(new_row) - 1)
        range_name = f"'{sheet_name}'!A{next_row}:{end_col_char}{next_row}"
        
        updates.append({
            'range': range_name,
            'values': [new_row]
        })
        if len(updates) == 1 or len(updates) == len(rows_to_append):
             print(f"DEBUG: Appending row {next_row}. Row Len: {len(new_row)}. Range: {range_name}.")
             print(f"DEBUG: Last Val (JSON Link): '{new_row[-1]}'")
        next_row += 1

    # 3. Perform Batch Updates (All in one go)
    if updates:
        data = []
        for u in updates:
            data.append({
                'range': u['range'],
                'values': u['values']
            })
        
        body = {
            'valueInputOption': 'USER_ENTERED',
            'data': data
        }
        
        try:
            execute_with_retry(lambda: service.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet_id, body=body
            ).execute())
            print(f"Upserted {len(updates)} rows (Updates + Appends).")
        except Exception as e:
            print(f"Error batch updating: {e}")

def append_batch_to_sheet(service, sheet_id, rows, sheet_name="Feuille 1", retries=10):
    """
    Appends multiple rows to the sheet in one API call.
    rows: List of lists (e.g., [[val1, val2], [val3, val4]])
    """
    if not rows:
        return

    range_name = f"'{sheet_name}'!A:Z"
    body = {'values': rows}
    
    try:
        execute_with_retry(lambda: service.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body=body
        ).execute())
        print(f"Appended {len(rows)} rows to sheet.")
    except Exception as e:
        print(f"Error appending batch: {e}")

def ensure_report_headers(service, sheet_id, sheet_name):
    """
    Checks if the report sheet exists and has headers. 
    If it doesn't exist, creates it and writes headers.
    If it exists but is empty, writes headers.
    """
    print(f"Checking headers for sheet '{sheet_name}'...")
    
    headers = [
        "Prénom", "Nom", "Email", "Téléphone", "Adresse", 
        "Langues", "Années Expérience", "Dernier Titre", 
        "Dernière Localisation", "Lien MD", "Action", "Emplacement", "Lien JSON", "Lien CV"
    ]
    
    try:
        # Check first row
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1:N1"
        ).execute()
        values = result.get('values', [])
        
        if not values:
            print(f"Sheet '{sheet_name}' exists but is empty. Writing headers...")
            body = {'values': [headers]}
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            print("Headers written successfully.")
        else:
            # Check if "Action" header is present (index 10)
            if len(values[0]) < 11 or values[0][10] != "Action":
                 print("Adding missing 'Action' header...")
                 service.spreadsheets().values().update(
                    spreadsheetId=sheet_id, range=f"'{sheet_name}'!K1",
                    valueInputOption="USER_ENTERED", body={'values': [["Action"]]}
                 ).execute()
            
            # Check if "Emplacement" header is present (index 11)
            if len(values[0]) < 12 or values[0][11] != "Emplacement":
                 print("Adding missing 'Emplacement' header...")
                 service.spreadsheets().values().update(
                    spreadsheetId=sheet_id, range=f"'{sheet_name}'!L1",
                    valueInputOption="USER_ENTERED", body={'values': [["Emplacement"]]}
                 ).execute()

            # Check if "Lien JSON" header is present (index 12)
            if len(values[0]) < 13 or values[0][12] != "Lien JSON":
                 print("Adding missing 'Lien JSON' header...")
                 service.spreadsheets().values().update(
                    spreadsheetId=sheet_id, range=f"'{sheet_name}'!M1",
                    valueInputOption="USER_ENTERED", body={'values': [["Lien JSON"]]}
                 ).execute()

            # Check if "Lien CV" header is present (index 13)
            if len(values[0]) < 14 or values[0][13] != "Lien CV":
                 print("Adding missing 'Lien CV' header...")
                 service.spreadsheets().values().update(
                    spreadsheetId=sheet_id, range=f"'{sheet_name}'!N1",
                    valueInputOption="USER_ENTERED", body={'values': [["Lien CV"]]}
                 ).execute()

            print(f"Sheet '{sheet_name}' headers checked.")
            
        # Set Validation for Action Column (K)
        set_column_validation(service, sheet_id, sheet_name, 
                            col_index=10, # K is index 10 (0-based)
                            options=["Retraiter", "Supprimer"])
            
    except Exception as e:
        # If error is likely "Sheet not found"
        print(f"Sheet '{sheet_name}' not found or inaccessible ({e}). Attempting to create it...")
        try:
            body = {
                'requests': [{
                    'addSheet': {
                        'properties': {
                            'title': sheet_name
                        }
                    }
                }]
            }
            service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
            print(f"Created new sheet '{sheet_name}'.")
            
            # Now write headers
            body = {'values': [headers]}
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            print("Headers written to new sheet.")
            
            # Set Validation
            set_column_validation(service, sheet_id, sheet_name, 
                                col_index=10, 
                                options=["Retraiter", "Supprimer"])
            
        except Exception as create_error:
            print(f"Failed to create sheet '{sheet_name}': {create_error}")
        raise

def remove_empty_rows(service, sheet_id, sheet_name):
    """
    Finds and removes empty rows from the specified sheet.
    Deletes rows that have no values.
    """
    print(f"Checking for empty rows in '{sheet_name}'...")
    
    # 1. Get all values
    values = get_sheet_values(service, sheet_id, sheet_name)
    if not values:
        print("Sheet is empty.")
        return

    # 2. Identify empty rows (0-based index)
    empty_row_indices = []
    for i, row in enumerate(values):
        # Check if row is empty or all cells are empty strings
        if not row or all(str(cell).strip() == "" for cell in row):
            empty_row_indices.append(i)
            
    if not empty_row_indices:
        print("No empty rows found.")
        return

    print(f"Found {len(empty_row_indices)} empty rows. Removing...")

    # 3. Group into contiguous ranges to minimize requests
    # e.g. [2, 3, 4, 8, 10] -> [(2, 4), (8, 8), (10, 10)]
    ranges = []
    if empty_row_indices:
        start = empty_row_indices[0]
        end = start
        for i in empty_row_indices[1:]:
            if i == end + 1:
                end = i
            else:
                ranges.append((start, end))
                start = i
                end = i
        ranges.append((start, end))

    # 4. Create Delete Requests (Reverse Order is CRITICAL)
    # We must delete from bottom to top so indices of earlier rows don't change
    ranges.sort(key=lambda x: x[0], reverse=True)
    
    # Get sheetId
    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    sheet_int_id = 0
    for s in sheets:
        if s.get("properties", {}).get("title") == sheet_name:
            sheet_int_id = s.get("properties", {}).get("sheetId")
            break

    requests = []
    for start_idx, end_idx in ranges:
        # deleteDimension uses startIndex (inclusive) and endIndex (exclusive)
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_int_id,
                    "dimension": "ROWS",
                    "startIndex": start_idx,
                    "endIndex": end_idx + 1
                }
            }
        })

    body = {'requests': requests}
    
    try:
        service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
        print(f"Successfully removed {len(empty_row_indices)} empty rows.")
    except Exception as e:
        print(f"Error removing empty rows: {e}")

def batch_update_rows(service, sheet_id, updates, sheet_name="Feuille 1", start_col='A', retries=10):
    """
    Updates multiple rows in one batchUpdate call.
    updates: List of tuples (row_index_0_based, values_list)
    start_col: The starting column letter (e.g., 'A', 'I').
    """
    if not updates:
        return

    # Get sheetId with retry
    sheet_int_id = 0
    try:
        sheet_metadata = execute_with_retry(lambda: service.spreadsheets().get(spreadsheetId=sheet_id).execute())
        sheets = sheet_metadata.get('sheets', '')
        for s in sheets:
            if s.get("properties", {}).get("title") == sheet_name:
                sheet_int_id = s.get("properties", {}).get("sheetId")
                break
    except Exception as e:
        print(f"Error getting sheet metadata: {e}")
        return
            
    data = []
    for row_idx, values in updates:
        sheet_row_num = row_idx + 1
        # Calculate end column based on start_col and length of values
        start_col_idx = 0
        if len(start_col) == 1:
            start_col_idx = ord(start_col.upper()) - ord('A')
        
        end_col_idx = start_col_idx + len(values) - 1
        
        # Convert indices back to letters (simple implementation for A-Z)
        # For > Z, this simple logic fails, but we are likely within A-Z for now.
        # If needed, we can implement a proper col index to letter function.
        if start_col_idx > 25 or end_col_idx > 25:
             # Fallback or simple support for AA, AB etc if needed, but let's assume A-Z for now
             pass
             
        start_col_char = chr(ord('A') + start_col_idx)
        end_col_char = chr(ord('A') + end_col_idx)
        
        range_name = f"'{sheet_name}'!{start_col_char}{sheet_row_num}:{end_col_char}{sheet_row_num}"
        data.append({
            'range': range_name,
            'values': [values]
        })
        
    body = {
        'valueInputOption': 'USER_ENTERED',
        'data': data
    }
    
    try:
        execute_with_retry(lambda: service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body=body
        ).execute())
        print(f"Batch updated {len(updates)} rows starting at col {start_col}.")
    except Exception as e:
        print(f"Error batch updating: {e}")
        raise

def delete_rows(service, sheet_id, row_indices, sheet_name="Feuille 1", retries=10):
    """
    Deletes specific rows from the sheet.
    row_indices: List of 0-based row indices to delete.
    IMPORTANT: Indices must be processed in descending order to avoid shifting issues,
    but the API handles batch requests atomically. However, defining ranges is easier if we group them.
    Actually, deleteDimension takes a range.
    """
    if not row_indices:
        return

    # Get sheetId
    sheet_metadata = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheets = sheet_metadata.get('sheets', '')
    sheet_int_id = 0
    for s in sheets:
        if s.get("properties", {}).get("title") == sheet_name:
            sheet_int_id = s.get("properties", {}).get("sheetId")
            break
            
    # Sort indices descending to be safe, though batchUpdate handles it if we specify ranges correctly.
    # But wait, if we delete row 10 and row 11.
    # If we send delete row 10, then row 11 becomes row 10.
    # So we MUST delete from bottom up if we send separate requests.
    # OR we can group contiguous ranges.
    
    row_indices = sorted(list(set(row_indices)), reverse=True)
    
    requests = []
    for row_idx in row_indices:
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_int_id,
                    "dimension": "ROWS",
                    "startIndex": row_idx,
                    "endIndex": row_idx + 1
                }
            }
        })
    
    if requests:
        body = {'requests': requests}
        try:
            service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
            print(f"Deleted {len(row_indices)} rows.")
        except Exception as e:
            print(f"Error deleting rows: {e}")

def remove_duplicates_by_column(service, sheet_id, sheet_name, col_index=2):
    """
    Removes rows that have duplicate values in the specified column.
    Smart Logic:
    1. If duplicates exist, check "Dernier Titre" (Column H, Index 7).
    2. If one is "NON-CV" and another is a real CV, delete the "NON-CV".
    3. Otherwise, keep the FIRST occurrence.
    col_index: 0-based index of the column to check (default 2 for Email).
    """
    print(f"Removing duplicates based on column index {col_index} (Smart Mode)...")
    rows = get_sheet_values(service, sheet_id, sheet_name)
    
    if not rows:
        return

    # Map: Email -> List of (row_index, is_non_cv)
    email_map = {}
    
    for i, row in enumerate(rows):
        if i == 0: continue # Skip header
        
        if len(row) > col_index:
            val = str(row[col_index]).strip().lower()
            if val:
                # Check if NON-CV (Column H / Index 7)
                is_non_cv = False
                if len(row) > 7:
                    title = str(row[7]).strip().upper()
                    if title == "NON-CV":
                        is_non_cv = True
                
                if val not in email_map:
                    email_map[val] = []
                email_map[val].append({'index': i, 'is_non_cv': is_non_cv})
    
    rows_to_delete = []
    
    for email, entries in email_map.items():
        if len(entries) > 1:
            # Check if we have a mix of CV and NON-CV
            has_cv = any(not e['is_non_cv'] for e in entries)
            has_non_cv = any(e['is_non_cv'] for e in entries)
            
            if has_cv and has_non_cv:
                # Delete ALL NON-CVs
                for e in entries:
                    if e['is_non_cv']:
                        rows_to_delete.append(e['index'])
                
                # If we still have multiple CVs left, keep only the first one
                cv_entries = [e for e in entries if not e['is_non_cv']]
                if len(cv_entries) > 1:
                    # Keep first (lowest index), delete others
                    # Sort by index just in case
                    cv_entries.sort(key=lambda x: x['index'])
                    for e in cv_entries[1:]:
                        rows_to_delete.append(e['index'])
                        
            else:
                # All are CVs OR All are NON-CVs -> Keep First
                entries.sort(key=lambda x: x['index'])
                for e in entries[1:]:
                    rows_to_delete.append(e['index'])
    
    if rows_to_delete:
        # Remove duplicates from list and sort reverse
        rows_to_delete = sorted(list(set(rows_to_delete)), reverse=True)
        print(f"Found {len(rows_to_delete)} rows to delete (Smart Deduplication).")
        delete_rows(service, sheet_id, rows_to_delete, sheet_name)
    else:
        print("No duplicates found.")

def create_hyperlink_formula(url, name):
    """
    Generates a valid French Excel Hyperlink formula.
    Format: =LIEN_HYPERTEXTE("url"; "name")
    """
    # Escape double quotes in name if necessary
    safe_name = name.replace('"', '""')
    formula = f'=LIEN_HYPERTEXTE("{url}"; "{safe_name}")'
    print(f"DEBUG: Generated Formula: {formula}")
    return formula
