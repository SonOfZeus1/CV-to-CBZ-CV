import os
import argparse
import shutil
import logging
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet
import re
import difflib
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet
from parsers import extract_text_from_pdf, extract_text_from_docx

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TEMP_DIR = "temp_cvs"

def select_best_email(emails, filename):
    """
    Selects the best email from a list based on similarity to the filename.
    """
    if not emails:
        return ""
    if len(emails) == 1:
        return emails[0]
    
    # Normalize filename: remove extension, lower case, split by non-alphanumeric
    fname_base = os.path.splitext(filename)[0].lower()
    fname_parts = re.split(r'[^a-z0-9]', fname_base)
    fname_parts = [p for p in fname_parts if len(p) > 2] # Filter short parts like 'cv', 'de', etc.
    
    best_email = emails[0]
    max_score = -1.0
    
    for email in emails:
        score = 0.0
        email_lower = email.lower()
        
        # Check if name parts are in email (high weight)
        for part in fname_parts:
            if part in email_lower:
                score += 1.0
        
        # Tie-breaker: similarity ratio (0 to 1)
        ratio = difflib.SequenceMatcher(None, fname_base, email_lower).ratio()
        score += ratio
        
        logger.info(f"Email candidate: {email}, Score: {score:.2f}")
        
        if score > max_score:
            max_score = score
            best_email = email
            
    return best_email

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
                # Limit to first 2000 characters (approx 1 page) to avoid Reference emails
                text_head = text[:2000]
                
                # Find all emails using regex
                email_pattern = r"[\w\.-]+@[\w\.-]+\.\w+"
                emails = list(set(re.findall(email_pattern, text_head))) # unique
                
                email = select_best_email(emails, filename)
                
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
