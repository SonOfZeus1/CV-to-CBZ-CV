import os
import argparse
import shutil
import logging
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet
import re
import difflib
from google_drive import get_drive_service, get_sheets_service, download_files_from_folder, append_to_sheet, get_sheet_values, clear_and_write_sheet, format_header_row, update_sheet_row
from parsers import extract_text_from_pdf, extract_text_from_docx, heuristic_parse_contact

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

def split_name(full_name):
    """
    Splits a full name into First Name and Last Name.
    Simple heuristic: Last word is Last Name, rest is First Name.
    """
    if not full_name:
        return "", ""
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]

def clean_phone_number(phone):
    """
    Formats phone number to (xxx) xxx-xxxx and prepends ' to force text format in Excel.
    """
    if not phone:
        return ""
    
    # Remove non-digits
    digits = re.sub(r'\D', '', phone)
    
    if len(digits) == 10:
        formatted = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        return f"'{formatted}"
    elif len(digits) == 11 and digits.startswith('1'):
        formatted = f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        return f"'{formatted}"
    else:
        # Return original with ' prepended if it has digits
        if digits:
            return f"'{phone}"
        return phone

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
    """
    Downloads CVs from a Drive folder, extracts emails, and saves them to a Sheet.
    """
    # 1. Authenticate
    logger.info("Authenticating with Google Services...")
    drive_service = get_drive_service()
    sheets_service = get_sheets_service()

    # 2. Deduplicate existing data first
    deduplicate_sheet(sheets_service, sheet_id, sheet_name)
    
    # Load existing data to check for duplicates AND missing info
    # Map: filename -> {index: int, email: str, phone: str}
    existing_rows = get_sheet_values(sheets_service, sheet_id, sheet_name)
    existing_data_map = {}
    
    if existing_rows:
        for i, row in enumerate(existing_rows):
            if i == 0: continue # Skip header
            # Row: [Filename, Email, Phone, First, Last]
            # Filename might be a formula like =HYPERLINK("...", "name.pdf")
            # We need to extract the name.
            raw_filename = row[0] if len(row) > 0 else ""
            
            # Simple extraction if it's a formula
            match = re.search(r'"([^"]+)"\)$', raw_filename)
            clean_filename = match.group(1) if match else raw_filename
            
            email = row[1] if len(row) > 1 else ""
            phone = row[2] if len(row) > 2 else ""
            
            existing_data_map[clean_filename] = {
                'index': i,
                'email': email.strip(),
                'phone': phone.strip()
            }

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
        for file_data in downloaded_files:
            file_path = file_data['path']
            filename = file_data['name']
            file_link = file_data['link']
            
            # Check if we need to process this file
            should_process = True
            row_index_to_update = -1
            
            if filename in existing_data_map:
                data = existing_data_map[filename]
                # Check if Email OR Phone is missing
                if not data['email'] or not data['phone'] or data['email'] == "NOT FOUND":
                    logger.info(f"Reprocessing {filename}: Missing Email or Phone.")
                    row_index_to_update = data['index']
                else:
                    logger.info(f"Skipping {filename}: Already complete.")
                    should_process = False
            
            if not should_process:
                continue

            logger.info(f"Processing: {filename}")
            
            try:
                # Extract Text
                text = ""
                _, ext = os.path.splitext(filename)
                if ext.lower() == '.pdf':
                    text, _ = extract_text_from_pdf(file_path)
                elif ext.lower() == '.docx':
                    text = extract_text_from_docx(file_path)
                
                # Create Hyperlink Formula
                filename_cell = f'=HYPERLINK("{file_link}", "{filename}")' if file_link else filename
                
                if not text:
                    logger.warning(f"Could not extract text from {filename}")
                    continue

                # Extract Email
                # Limit to first 2000 characters (approx 1 page) to avoid Reference emails
                text_head = text[:2000]
                
                # Find all emails using regex
                email_pattern = r"[\w\.-]+@[\w\.-]+\.\w+"
                emails = list(set(re.findall(email_pattern, text_head))) # unique
                
                email = select_best_email(emails, filename)
                
                # Extract other info
                contact_info = heuristic_parse_contact(text_head)
                phone = contact_info.get('phone', '')
                full_name = contact_info.get('name', '')
                
                # Clean Phone
                phone = clean_phone_number(phone)
                
                # If name is empty, try to use filename base
                if not full_name:
                        full_name = os.path.splitext(filename)[0].replace("_", " ").replace("-", " ")
                
                first_name, last_name = split_name(full_name)
                
                # Prepare Row Data
                # If email not found, keep it empty or "NOT FOUND"
                email_val = email if email else "NOT FOUND"
                
                row_data = [filename_cell, email_val, phone, first_name, last_name]
                
                if row_index_to_update != -1:
                    # Update existing row
                    update_sheet_row(sheets_service, sheet_id, row_index_to_update, row_data, sheet_name=sheet_name)
                else:
                    # Append new row
                    append_to_sheet(sheets_service, sheet_id, row_data, sheet_name=sheet_name)
                    
            except Exception as e:
                logger.error(f"Error processing {filename}: {e}")

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
