import os
import argparse
import shutil
import logging
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet
import re
import difflib
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet, get_sheet_values, clear_and_write_sheet, format_header_row
from parsers import extract_text_from_pdf, extract_text_from_docx, heuristic_parse_contact

# ... (imports and logging)

# ... (select_best_email and split_name functions)

def deduplicate_sheet(sheets_service, sheet_id, sheet_name):
    """
    Reads the sheet, removes rows with duplicate emails, and rewrites it.
    Ensures header exists.
    """
    logger.info("Running deduplication...")
    rows = get_sheet_values(sheets_service, sheet_id, sheet_name)
    
    expected_header = ["Filename", "Email", "Phone", "First Name", "Last Name"]
    
    if not rows:
        # Sheet is empty, write header
        clear_and_write_sheet(sheets_service, sheet_id, [expected_header], sheet_name)
        format_header_row(sheets_service, sheet_id, sheet_name)
        return

    header = rows[0]
    data = rows[1:]
    
    # Check if header matches expected (loose check)
    if header != expected_header:
        # If first row looks like data (e.g. contains email), prepend header
        # Simple check: "Email" not in header
        if "Email" not in header:
            data = rows # All rows are data
            header = expected_header
        else:
            # Header exists but might be different, let's force update it if needed?
            # Or just keep it. Let's keep it but ensure we use our expected header for new sheet if we rewrite.
            pass

    seen_emails = set()
    unique_rows = []
    
    # Keep header
    unique_rows.append(expected_header) # Enforce standard header
    
    for row in data:
        # Assuming Email is in column 2 (index 1)
        if len(row) > 1:
            email = row[1].lower().strip()
            if email and email not in seen_emails:
                seen_emails.add(email)
                unique_rows.append(row)
            elif not email:
                unique_rows.append(row)
        else:
            unique_rows.append(row)
            
    # Always rewrite to ensure header is correct and formatted
    clear_and_write_sheet(sheets_service, sheet_id, unique_rows, sheet_name)
    format_header_row(sheets_service, sheet_id, sheet_name)
    logger.info(f"Deduplication complete. Total rows: {len(unique_rows)}")

def process_folder(folder_id, sheet_id, sheet_name="Feuille 1"):
    # ... (rest of function)
    """
    Downloads CVs from a Drive folder, extracts emails, and saves them to a Sheet.
    """
    # 1. Authenticate
    logger.info("Authenticating with Google Services...")
    drive_service = get_drive_service()
    sheets_service = get_sheets_service()

    # 2. Deduplicate existing data first (optional, but good practice)
    deduplicate_sheet(sheets_service, sheet_id, sheet_name)
    
    # Load existing emails to prevent adding new duplicates
    existing_rows = get_sheet_values(sheets_service, sheet_id, sheet_name)
    existing_emails = set()
    if existing_rows:
        for row in existing_rows[1:]: # Skip header
             if len(row) > 1:
                 existing_emails.add(row[1].lower().strip())

    # 3. Create Temp Directory
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    try:
        # 4. Download Files
        logger.info(f"Downloading files from folder ID: {folder_id}")
        downloaded_files = download_files_from_folder(drive_service, folder_id, TEMP_DIR)
        
        if not downloaded_files:
            logger.warning("No files found or downloaded.")
            return

        # 5. Process Each File
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
                    # append_to_sheet(sheets_service, sheet_id, [filename, "ERROR: No text extracted"], sheet_name=sheet_name)
                    continue

                # Extract Email
                # Limit to first 2000 characters (approx 1 page) to avoid Reference emails
                text_head = text[:2000]
                
                # Find all emails using regex
                email_pattern = r"[\w\.-]+@[\w\.-]+\.\w+"
                emails = list(set(re.findall(email_pattern, text_head))) # unique
                
                email = select_best_email(emails, filename)
                
                if email:
                    # Check for duplicates
                    if email.lower().strip() in existing_emails:
                        logger.info(f"Email {email} already exists in sheet. Skipping.")
                        continue
                        
                    logger.info(f"Found email: {email}")
                    
                    # Extract other info
                    contact_info = heuristic_parse_contact(text_head)
                    phone = contact_info.get('phone', '')
                    full_name = contact_info.get('name', '')
                    
                    # If name is empty, try to use filename base
                    if not full_name:
                         full_name = os.path.splitext(filename)[0].replace("_", " ").replace("-", " ")
                    
                    first_name, last_name = split_name(full_name)
                    
                    # Append: [Filename, Email, Phone, FirstName, LastName]
                    row_data = [filename, email, phone, first_name, last_name]
                    append_to_sheet(sheets_service, sheet_id, row_data, sheet_name=sheet_name)
                    
                    # Add to local set
                    existing_emails.add(email.lower().strip())
                    
                else:
                    logger.warning(f"No email found in {filename}")
                    # append_to_sheet(sheets_service, sheet_id, [filename, "NOT FOUND"], sheet_name=sheet_name)

            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")
                # append_to_sheet(sheets_service, sheet_id, [filename, f"ERROR: {str(e)}"], sheet_name=sheet_name)

    finally:
        # 6. Cleanup
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
