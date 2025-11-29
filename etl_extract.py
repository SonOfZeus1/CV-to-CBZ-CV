import os
import json
import logging
import io
from dotenv import load_dotenv
from googleapiclient.http import MediaIoBaseDownload

from google_drive import get_drive_service, get_sheets_service, fetch_pending_cvs, update_cv_status, upload_file_to_folder
from parsers import parse_cv

# --- Configuration Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
DOWNLOADS_DIR = "downloaded_cvs"
JSON_OUTPUT_DIR = "JSON generated"

def download_file_by_id(drive_service, file_id, file_name, destination_folder):
    """Downloads a specific file by ID."""
    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
        
    file_path = os.path.join(destination_folder, file_name)
    
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            
        with open(file_path, 'wb') as f:
            f.write(fh.getvalue())
        return file_path
    except Exception as e:
        logger.error(f"Error downloading {file_name} ({file_id}): {e}")
        return None

def process_extract_row(row_data, drive_service, sheets_service, sheet_id, output_folder_id, sheet_name):
    """
    Pipeline 1: Extraction
    PDF -> JSON
    """
    row_num = row_data['row']
    file_id = row_data['file_id']
    file_name = row_data['file_name']
    
    logger.info(f"EXTRACT Row {row_num}: {file_name}")
    
    # Update status to PROCESSING
    update_cv_status(sheets_service, sheet_id, row_num, "EXTRACTION_EN_COURS", sheet_name=sheet_name)
    
    # 1. Download PDF
    local_path = download_file_by_id(drive_service, file_id, file_name, DOWNLOADS_DIR)
    if not local_path:
        update_cv_status(sheets_service, sheet_id, row_num, "ERREUR_DOWNLOAD", sheet_name=sheet_name)
        return

    try:
        # 2. Parse (AI Extraction)
        parsed_data = parse_cv(local_path)
        
        if not parsed_data:
            update_cv_status(sheets_service, sheet_id, row_num, "ERREUR_PARSING", sheet_name=sheet_name)
            return

        # 3. Save JSON Locally
        base_name = os.path.splitext(file_name)[0]
        json_filename = f"{base_name}_extracted.json"
        if not os.path.exists(JSON_OUTPUT_DIR):
            os.makedirs(JSON_OUTPUT_DIR)
        json_output_path = os.path.join(JSON_OUTPUT_DIR, json_filename)
        
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
        # 4. Upload JSON to Drive
        json_file_id = upload_file_to_folder(drive_service, json_output_path, output_folder_id)
        
        # Get Web View Link for JSON
        file_info = drive_service.files().get(fileId=json_file_id, fields='webViewLink').execute()
        json_link = file_info.get('webViewLink', '')
        
        # 5. Update Sheet -> JSON_OK
        # We leave PDF Link and Summary empty for now
        update_cv_status(sheets_service, sheet_id, row_num, "JSON_OK", sheet_name=sheet_name, json_link=json_link)
        logger.info(f"SUCCESS EXTRACT Row {row_num}: {file_name}")

    except Exception as e:
        logger.error(f"Error extracting {file_name}: {e}", exc_info=True)
        update_cv_status(sheets_service, sheet_id, row_num, f"ERREUR: {str(e)}", sheet_name=sheet_name)

def main():
    load_dotenv()
    logger.info("--- Starting Pipeline 1: EXTRACTION (PDF -> JSON) ---")

    sheet_id = os.environ.get('SHEET_ID')
    source_folder_id = os.environ.get('SOURCE_FOLDER_ID')
    json_output_folder_id = os.environ.get('JSON_OUTPUT_FOLDER_ID')
    sheet_name = os.environ.get('SHEET_NAME', 'Feuille 1')
    
    if not sheet_id or not source_folder_id:
        logger.error("Missing SHEET_ID or SOURCE_FOLDER_ID in .env")
        return

    if not json_output_folder_id:
        logger.warning("JSON_OUTPUT_FOLDER_ID not set. Using SOURCE_FOLDER_ID as fallback.")
        json_output_folder_id = source_folder_id

    try:
        drive_service = get_drive_service()
        sheets_service = get_sheets_service()
    except Exception as e:
        logger.critical(f"Auth Error: {e}")
        return

    # Fetch rows with status "EN_ATTENTE"
    logger.info(f"Fetching pending CVs (EN_ATTENTE) from {sheet_name}...")
    pending_rows = fetch_pending_cvs(sheets_service, sheet_id, sheet_name=sheet_name, target_status="EN_ATTENTE")
    
    if not pending_rows:
        logger.info("No pending CVs found.")
        return
        
    logger.info(f"Found {len(pending_rows)} CVs to extract.")

    for row in pending_rows:
        process_extract_row(row, drive_service, sheets_service, sheet_id, json_output_folder_id, sheet_name)

    logger.info("--- Extraction Pipeline Finished ---")

if __name__ == "__main__":
    main()
