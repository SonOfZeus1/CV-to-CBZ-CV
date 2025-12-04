import os
import argparse
import shutil
import logging
from google_drive import get_drive_service, get_sheets_service, list_files_in_folder, download_file, append_to_sheet
import re
import difflib
from google_drive import get_drive_service, get_sheets_service, list_files_in_folder, download_file, append_to_sheet, get_sheet_values, clear_and_write_sheet, format_header_row, update_sheet_row
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
    Strategy:
    1. Group rows by Email.
    2. If multiple rows have the same email, keep the one with the SHORTEST filename (Column A).
       (Assumption: Longer filenames are often copies like "CV (1).pdf").
    3. Rows without email are preserved.
    """
    logger.info("Running deduplication...")
    # Use FORMULA render option to get the raw hyperlink formula for length comparison
    rows = get_sheet_values(sheets_service, sheet_id, sheet_name, value_render_option='FORMULA')
    
    expected_header = ["Filename", "Email", "Phone", "Status", "JSON Link"]
    
    if not rows:
        # Sheet is empty, write header
        clear_and_write_sheet(sheets_service, sheet_id, [expected_header], sheet_name)
        format_header_row(sheets_service, sheet_id, sheet_name)
        return

    header = rows[0]
    data = rows[1:]
    
    # Check if header matches expected (loose check)
    if header != expected_header:
        if "Email" not in header:
            data = rows # All rows are data
            header = expected_header
        else:
            # If header exists but is missing columns, we might want to update it?
            # For now, let's assume it's fine or user will fix.
            pass

    email_groups = {} # email -> list of rows
    rows_without_email = []

    for row in data:
        # Ensure row has at least 5 columns (pad if needed)
        while len(row) < 5:
            row.append("")
            
        # Assuming Email is in column 2 (index 1)
        email = str(row[1]).lower().strip()
        
        if email and email != "not found":
            if email not in email_groups:
                email_groups[email] = []
            email_groups[email].append(row)
        else:
            rows_without_email.append(row)
            
    unique_data = []
    
    # Process groups
    for email, group in email_groups.items():
        if len(group) == 1:
            unique_data.append(group[0])
        else:
            # Sort by length of filename (row[0]), ascending.
            group.sort(key=lambda r: len(r[0]))
            unique_data.append(group[0])
            
    # Combine: Header + Rows without Email + Unique Rows
    final_rows = [expected_header] + rows_without_email + unique_data
    
    # Always rewrite to ensure header is correct and formatted
    clear_and_write_sheet(sheets_service, sheet_id, final_rows, sheet_name)
    format_header_row(sheets_service, sheet_id, sheet_name)
    logger.info(f"Deduplication complete. Reduced from {len(data)} to {len(final_rows)-1} rows.")

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
    # Map: filename -> {index: int, email: str, phone: str, is_hyperlink: bool, needs_fix: bool}
    # Use value_render_option='FORMULA' to see the actual formula even if it errors
    existing_rows = get_sheet_values(sheets_service, sheet_id, sheet_name, value_render_option='FORMULA')
    existing_data_map = {}
    
    if existing_rows:
        for i, row in enumerate(existing_rows):
            if i == 0: continue # Skip header
            # Row: [Filename, Email, Phone, Status, JSON Link]
            raw_filename = row[0] if len(row) > 0 else ""
            
            # Check if it's a hyperlink formula
            is_hyperlink = raw_filename.startswith('=HYPERLINK')
            
            # Check if it uses the correct separator (semicolon for French)
            uses_semicolon = ';' in raw_filename if is_hyperlink else False
            
            # Extract clean filename
            if is_hyperlink:
                match = re.search(r'"([^"]+)"\)$', raw_filename)
                clean_filename = match.group(1) if match else raw_filename
            else:
                clean_filename = raw_filename
            
            email = row[1] if len(row) > 1 else ""
            phone = row[2] if len(row) > 2 else ""
            
            existing_data_map[clean_filename] = {
                'index': i,
                'email': str(email).strip(),
                'phone': str(phone).strip(),
                'is_hyperlink': is_hyperlink,
                'needs_fix': is_hyperlink and not uses_semicolon
            }

    # 3. Create Temp Directory
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    try:
        # 4. List Files (Metadata only)
        logger.info(f"Listing files from folder ID: {folder_id}")
        all_files = list_files_in_folder(drive_service, folder_id)
        
        if not all_files:
            logger.warning("No files found in Drive folder.")
            return

        logger.info(f"Found {len(all_files)} files in Drive.")

        # 5. Process Each File
        for file_data in all_files:
            file_id = file_data['id']
            filename = file_data['name']
            file_link = file_data['link']
            
            # Create Hyperlink Formula
            safe_filename = filename.replace('"', '""')
            filename_cell = f'=HYPERLINK("{file_link}"; "{safe_filename}")' if file_link else filename
            
            # Check if we need to process this file
            should_full_process = True
            row_index_to_update = -1
            use_existing_data = False
            existing_data = None
            
            if filename in existing_data_map:
                data = existing_data_map[filename]
                row_index_to_update = data['index']
                existing_data = data
                
                # Condition 1: Missing Email or Phone -> Full Process
                # Check for "NOT FOUND" case-insensitively
                # Also checks if email is empty/null
                if not data['email'] or not data['phone'] or data['email'].upper() == "NOT FOUND":
                    logger.info(f"Reprocessing {filename}: Missing Email/Phone or Email is 'NOT FOUND'.")
                    should_full_process = True
                
                # Condition 2: Missing Hyperlink OR Broken Formula -> Update Link Only
                # This covers:
                # - Empty Filename cells (is_hyperlink=False)
                # - Text-only Filename cells (is_hyperlink=False)
                # - Broken Formulas (needs_fix=True)
                elif not data['is_hyperlink'] or data['needs_fix']:
                    reason = "Missing hyperlink" if not data['is_hyperlink'] else "Broken formula (wrong separator)"
                    logger.info(f"Updating Link for {filename}: {reason}.")
                    should_full_process = False
                    use_existing_data = True
                
                # Condition 3: All Good -> Skip
                else:
                    should_full_process = False
                    use_existing_data = False
                    continue # Explicitly skip
            
            # If we are here, we either need full process or just update link
            
            if use_existing_data and existing_data:
                # Construct row with new link and existing data
                # Preserve existing Status and JSON Link if they exist (need to fetch them from sheet? 
                # Actually existing_data_map doesn't store them. 
                # But since we are updating the row, we should probably preserve them.
                # However, update_sheet_row overwrites the range.
                # Let's just write empty strings for now or fetch the full row?
                # Optimization: For now, just write the first 3 columns. 
                # update_sheet_row takes values. If we pass 3 values, does it clear the rest?
                # No, update overwrites only the cells provided in the range.
                # So if we update A:C, D and E are safe.
                
                row_data = [
                    filename_cell, 
                    existing_data['email'], 
                    existing_data['phone']
                ]
                # We need to make sure update_sheet_row uses A:C range.
                update_sheet_row(sheets_service, sheet_id, row_index_to_update, row_data, sheet_name=sheet_name)
                continue # Done with this file

            if should_full_process:
                logger.info(f"Processing content: {filename}")
                try:
                    # DOWNLOAD FILE ON DEMAND
                    file_path = download_file(drive_service, file_id, filename, TEMP_DIR)
                    
                    # Extract Text
                    text = ""
                    _, ext = os.path.splitext(filename)
                    if ext.lower() == '.pdf':
                        text, _ = extract_text_from_pdf(file_path)
                    elif ext.lower() == '.docx':
                        text = extract_text_from_docx(file_path)
                    
                    # Remove file after processing to save space
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    
                    if not text:
                        logger.warning(f"Could not extract text from {filename}")
                        # Still add to sheet with empty data
                        row_data = [filename_cell, "NOT FOUND", "", "", ""]
                        if row_index_to_update != -1:
                            update_sheet_row(sheets_service, sheet_id, row_index_to_update, row_data, sheet_name=sheet_name)
                        else:
                            append_to_sheet(sheets_service, sheet_id, row_data, sheet_name=sheet_name)
                        continue

                    # Extract Email
                    text_head = text[:2000]
                    email_pattern = r"[\w\.-]+@[\w\.-]+\.\w+"
                    emails = list(set(re.findall(email_pattern, text_head)))
                    email = select_best_email(emails, filename)
                    
                    # Extract other info
                    contact_info = heuristic_parse_contact(text_head)
                    phone = contact_info.get('phone', '')
                    
                    # Clean Phone
                    phone = clean_phone_number(phone)
                    
                    # Prepare Row Data
                    email_val = email if email else "NOT FOUND"
                    
                    # Add empty Status and JSON Link
                    row_data = [filename_cell, email_val, phone, "", ""]
                    
                    if row_index_to_update != -1:
                        # If updating, we might want to preserve status? 
                        # If we are reprocessing, maybe reset status?
                        # Let's assume if we reprocess, we keep status empty or reset it.
                        update_sheet_row(sheets_service, sheet_id, row_index_to_update, row_data, sheet_name=sheet_name)
                    else:
                        append_to_sheet(sheets_service, sheet_id, row_data, sheet_name=sheet_name)
                        
                except Exception as e:
                    logger.error(f"Error processing {filename}: {e}")

    finally:
        # 6. Cleanup
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            logger.info("Temporary directory cleaned up.")
            
        # 7. Set Data Validation for Status Column (Column D, index 3)
        logger.info("Setting data validation for Status column...")
        set_column_validation(sheets_service, sheet_id, sheet_name, 3, ["Oui", "Non", "Delete"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract emails from CVs in a Google Drive folder.")
    parser.add_argument("--folder_id", required=True, help="Google Drive Folder ID containing CVs")
    parser.add_argument("--sheet_id", required=True, help="Google Sheet ID to save results")
    parser.add_argument("--sheet_name", default="Feuille 1", help="Name of the sheet to write to (default: 'Feuille 1')")
    
    args = parser.parse_args()
    
    process_folder(args.folder_id, args.sheet_id, args.sheet_name)
