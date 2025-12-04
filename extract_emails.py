import os
import argparse
import shutil
import logging
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet
from parsers import extract_text_from_pdf, extract_text_from_docx, heuristic_parse_contact

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TEMP_DIR = "temp_cvs"

def process_folder(folder_id, sheet_id, sheet_name="Feuille 1"):
    """
    Downloads CVs from a Drive folder, extracts emails, and saves them to a Sheet.
    """
    # 1. Authenticate
    logger.info("Authenticating with Google Services...")
    drive_service = get_drive_service()
    sheets_service = get_sheets_service()

    # 2. Create Temp Directory
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    try:
        # 3. Download Files
        logger.info(f"Downloading files from folder ID: {folder_id}")
        downloaded_files = download_files_from_folder(drive_service, folder_id, TEMP_DIR)
        
        if not downloaded_files:
            logger.warning("No files found or downloaded.")
            return

        # 4. Process Each File
        for file_path in downloaded_files:
            filename = os.path.basename(file_path)
            logger.info(f"Processing: {filename}")
            
            try:
                # Extract Text
                text = ""
                _, ext = os.path.splitext(filename)
                if ext.lower() == '.pdf':
                    text, _ = extract_text_from_pdf(file_path)
                elif ext.lower() == '.docx':
                    text = extract_text_from_docx(file_path)
                
                if not text:
                    logger.warning(f"Could not extract text from {filename}")
                    append_to_sheet(sheets_service, sheet_id, [filename, "ERROR: No text extracted"], sheet_name=sheet_name)
                    continue

                # Extract Email
                contact_info = heuristic_parse_contact(text)
                email = contact_info.get('email', '')
                
                if email:
                    logger.info(f"Found email: {email}")
                    append_to_sheet(sheets_service, sheet_id, [filename, email], sheet_name=sheet_name)
                else:
                    logger.warning(f"No email found in {filename}")
                    append_to_sheet(sheets_service, sheet_id, [filename, "NOT FOUND"], sheet_name=sheet_name)

            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")
                append_to_sheet(sheets_service, sheet_id, [filename, f"ERROR: {str(e)}"], sheet_name=sheet_name)

    finally:
        # 5. Cleanup
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            logger.info("Temporary directory cleaned up.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract emails from CVs in a Google Drive folder.")
    parser.add_argument("--folder_id", required=True, help="Google Drive Folder ID containing CVs")
    parser.add_argument("--sheet_id", required=True, help="Google Sheet ID to save results")
    parser.add_argument("--sheet_name", default="Feuille 1", help="Name of the sheet to write to (default: 'Feuille 1')")
    
    args = parser.parse_args()
    
    process_folder(args.folder_id, args.sheet_id, args.sheet_name)
