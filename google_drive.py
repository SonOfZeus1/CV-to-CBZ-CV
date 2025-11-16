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

    query = f"'{folder_id}' in parents and (mimeType='application/pdf' or mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
    
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
        fields='id',
        supportsAllDrives=True
    ).execute()
    print(f"Uploaded '{file_name}' with ID: {file.get('id')}")
    return file.get('id')

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
