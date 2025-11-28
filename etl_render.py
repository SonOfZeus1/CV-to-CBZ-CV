import os
import json
import logging
import io
from dotenv import load_dotenv
from googleapiclient.http import MediaIoBaseDownload

from google_drive import get_drive_service, get_sheets_service, fetch_pending_cvs, update_cv_status, upload_file_to_folder
from formatters import generate_pdf_from_data

# --- Configuration Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
DOWNLOADS_DIR = "downloaded_cvs" # We download JSONs here too
PDF_OUTPUT_DIR = "CV generated"
TEMPLATE_PATH = "templates/template.html"

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

def process_render_row(row_data, drive_service, sheets_service, sheet_id, output_folder_id):
    """
    Pipeline 2: Rendering
    JSON -> PDF
    """
    row_num = row_data['row']
    # Note: file_id here is the PDF file ID from the original row, but we need the JSON file ID.
    # The fetch_pending_cvs function returns 'json_link' but not the ID directly.
    # We need to extract ID from link or update fetcher to get ID if possible.
    # Actually, the JSON Link is just a link. It's safer to rely on the file ID if we stored it.
    # But we didn't store JSON ID in a dedicated column, just the link.
    # Hack: Extract ID from Drive Link: https://drive.google.com/file/d/FILE_ID/view...
    
    json_link = row_data.get('json_link', '')
    pdf_link_existing = row_data.get('pdf_link', '')
    
    # Skip if PDF already exists
    if pdf_link_existing:
        logger.info(f"Row {row_num}: PDF already exists. Skipping.")
        return

    if not json_link or "id=" not in json_link and "/d/" not in json_link:
        logger.error(f"Row {row_num}: Invalid JSON Link: {json_link}")
        update_cv_status(sheets_service, sheet_id, row_num, "ERREUR_LIEN_JSON")
        return

    # Extract ID
    json_file_id = ""
    if "/d/" in json_link:
        json_file_id = json_link.split("/d/")[1].split("/")[0]
    elif "id=" in json_link:
        json_file_id = json_link.split("id=")[1].split("&")[0]
        
    if not json_file_id:
        logger.error(f"Row {row_num}: Could not extract ID from {json_link}")
        update_cv_status(sheets_service, sheet_id, row_num, "ERREUR_ID_JSON")
        return
        
    original_filename = row_data['file_name']
    logger.info(f"RENDER Row {row_num}: {original_filename}")
    
    # We do NOT update status to RENDU_EN_COURS to avoid noise, or we can if we want.
    # User requested NO status change. So we skip this update or keep it but revert to JSON_OK at end?
    # "le pipeline 2 ne doit généré aucun status" -> implying it shouldn't change it permanently?
    # But if it fails, we might want to know.
    # I will skip the intermediate status update to be safe and strictly follow "no status generated".
    # update_cv_status(sheets_service, sheet_id, row_num, "RENDU_EN_COURS", json_link=json_link)
    
    # 1. Download JSON
    json_filename = f"render_{row_num}.json"
    local_json_path = download_file_by_id(drive_service, json_file_id, json_filename, DOWNLOADS_DIR)
    
    if not local_json_path:
        update_cv_status(sheets_service, sheet_id, row_num, "ERREUR_DOWNLOAD_JSON", json_link=json_link)
        return

    try:
        # 2. Read JSON
        with open(local_json_path, 'r', encoding='utf-8') as f:
            cv_data = json.load(f)
            
        # 3. Generate PDF
        base_name = os.path.splitext(original_filename)[0]
        pdf_output_path = os.path.join(PDF_OUTPUT_DIR, f"{base_name}_final.pdf")
        generate_pdf_from_data(cv_data, TEMPLATE_PATH, pdf_output_path)
        
        # 4. Upload PDF
        pdf_file_id = upload_file_to_folder(drive_service, pdf_output_path, output_folder_id)
        
        # Get Link
        file_info = drive_service.files().get(fileId=pdf_file_id, fields='webViewLink').execute()
        pdf_link = file_info.get('webViewLink', '')
        
        # 5. Get Summary (from JSON)
        summary = cv_data.get('summary', '')
        
        # 6. Update Sheet -> Keep JSON_OK, but add PDF Link
        update_cv_status(sheets_service, sheet_id, row_num, "JSON_OK", json_link=json_link, pdf_link=pdf_link, summary=summary)
        logger.info(f"SUCCESS RENDER Row {row_num}: {original_filename}")

    except Exception as e:
        logger.error(f"Error rendering {original_filename}: {e}", exc_info=True)
        update_cv_status(sheets_service, sheet_id, row_num, f"ERREUR_RENDU: {str(e)}", json_link=json_link)

def main():
    load_dotenv()
    logger.info("--- Starting Pipeline 2: RENDERING (JSON -> PDF) ---")

    sheet_id = os.environ.get('SHEET_ID')
    source_folder_id = os.environ.get('SOURCE_FOLDER_ID')
    json_input_folder_id = os.environ.get('JSON_INPUT_FOLDER_ID')
    pdf_output_folder_id = os.environ.get('PDF_OUTPUT_FOLDER_ID')
    
    if not sheet_id or not source_folder_id:
        logger.error("Missing SHEET_ID or SOURCE_FOLDER_ID in .env")
        return

    # Fallbacks
    if not json_input_folder_id:
         logger.warning("JSON_INPUT_FOLDER_ID not set. Assuming JSONs are in SOURCE_FOLDER_ID or linked correctly.")
         # We don't strictly need it for logic if we download by ID, but good to have.
    
    if not pdf_output_folder_id:
        logger.warning("PDF_OUTPUT_FOLDER_ID not set. Using SOURCE_FOLDER_ID as fallback for Output.")
        pdf_output_folder_id = source_folder_id

    try:
        drive_service = get_drive_service()
        sheets_service = get_sheets_service()
    except Exception as e:
        logger.critical(f"Auth Error: {e}")
        return

    # Fetch rows with status "JSON_OK"
    logger.info("Fetching ready CVs (JSON_OK)...")
    pending_rows = fetch_pending_cvs(sheets_service, sheet_id, target_status="JSON_OK")
    
    if not pending_rows:
        logger.info("No ready CVs found.")
        return
        
    logger.info(f"Found {len(pending_rows)} CVs to render.")
    if json_input_folder_id:
        logger.info(f"Input Folder (JSON): {json_input_folder_id}")
    logger.info(f"Output Folder (PDF): {pdf_output_folder_id}")

    for row in pending_rows:
        process_render_row(row, drive_service, sheets_service, sheet_id, pdf_output_folder_id)

    logger.info("--- Rendering Pipeline Finished ---")

if __name__ == "__main__":
    main()
