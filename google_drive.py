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

def download_files_from_folder(service, folder_id, download_path):
    """Downloads all .pdf and .docx files from a Google Drive folder."""
    if not os.path.exists(download_path):
        os.makedirs(download_path)

    # Correction Bug : Exclusion explicite des fichiers générés (_processed)
    # La clause "not name contains '_processed'" empêche la boucle infinie.
    query = (
        f"'{folder_id}' in parents "
        f"and (mimeType='application/pdf' or mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document') "
        f"and not name contains '_processed'"
    )
    
    print(f"--- LOG DE DÉBOGAGE GOOGLE DRIVE ---")
    print(f"Requête API envoyée : q={query}")
    
    results = service.files().list(
        q=query,
        fields="nextPageToken, files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    print(f"Réponse BRUTE de l'API : {results}")
    print(f"--- FIN DU LOG DE DÉBOGAGE ---")

    items = results.get('files', [])

    downloaded_files = []
    for item in items:
        file_id = item['id']
        file_name = item['name']
        file_path = os.path.join(download_path, file_name)

        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Downloading {file_name}: {int(status.progress() * 100)}%")

        with open(file_path, 'wb') as f:
            f.write(fh.getvalue())
        print(f"Downloaded '{file_name}' to '{file_path}'")
        downloaded_files.append(file_path)
    
    return downloaded_files

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
            print(f"Resetting Row {i} (Status: {status}) to EN_ATTENTE because JSON Link is empty.")
            update_cv_status(service, sheet_id, i, "EN_ATTENTE", sheet_name=sheet_name)
