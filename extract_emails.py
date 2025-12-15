import os
import argparse
import shutil
import logging
from google_drive import (
    get_drive_service, get_sheets_service, list_files_in_folder, 
    download_file, append_to_sheet, get_sheet_values, 
    clear_and_write_sheet, format_header_row, update_sheet_row,
    append_batch_to_sheet, batch_update_rows, set_column_validation,
    get_or_create_folder, move_file, delete_rows
)
import re
import difflib
from parsers import extract_text_from_pdf, extract_text_from_docx, heuristic_parse_contact
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def detect_language(text):
    """
    Detects if the text is English or French based on keywords.
    Returns 'EN', 'FR', or 'Unknown'.
    """
    if not text:
        return "Unknown"
        
    text_lower = text.lower()
    
    # Keywords
    fr_keywords = ['expérience', 'formation', 'compétences', 'langues', 'résumé', 'profil', 'éducation', 'janvier', 'février', 'août', 'décembre']
    en_keywords = ['experience', 'education', 'skills', 'languages', 'summary', 'profile', 'january', 'february', 'august', 'december']
    
    fr_score = sum(1 for k in fr_keywords if k in text_lower)
    en_score = sum(1 for k in en_keywords if k in text_lower)
    
    if fr_score > en_score:
        return "FR"
    elif en_score > fr_score:
        return "EN"
    else:
        return "Unknown"

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
    
    expected_header = ["Filename", "Email", "Phone", "Status", "JSON Link", "Language"]
    
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
            pass
    # 1. Identify rows to delete
    rows_to_delete = []
    
    # Iterate through data to find rows marked for deletion
    for i, row in enumerate(data):
        row_index = i + 1 # 0-based index in sheet (skipping header)
        
        # Ensure row has enough columns
        if len(row) < 4:
            continue
            
        # Check for "Delete" status
        status = str(row[3]).strip()
        if status.lower() == "delete":
            rows_to_delete.append(row_index)
            continue
            
    if rows_to_delete:
        logger.info(f"Deleting {len(rows_to_delete)} rows marked 'Delete'...")
        delete_rows(sheets_service, sheet_id, rows_to_delete, sheet_name)
    else:
        logger.info("No rows marked for deletion.")
        
    # format_header_row(sheets_service, sheet_id, sheet_name) # Optional, but good practice
    logger.info("Cleanup complete.")

def process_single_file(file_data, existing_data_map):
    """
    Processes a single file: checks if it needs processing, downloads, extracts info.
    Returns a dict with action ('APPEND', 'UPDATE', 'SKIP') and data.
    """
    # Create a thread-local Drive service to avoid SSL/Memory corruption
    drive_service = get_drive_service()
    
    file_id = file_data['id']
    filename = file_data['name']
    file_link = file_data.get('link', '')
    
    # Create Hyperlink Formula (French Locale)
    safe_filename = filename.replace('"', '""')
    # Use LIEN_HYPERTEXTE and semicolon for French locale
    filename_cell = f'=LIEN_HYPERTEXTE("{file_link}"; "{safe_filename}")' if file_link else filename
    
    # Check if we need to process this file
    should_full_process = True
    row_index_to_update = -1
    use_existing_data = False
    existing_data = None
    
    # Use File ID as key
    if file_id in existing_data_map:
        data = existing_data_map[file_id]
        row_index_to_update = data['index']
        existing_data = data
        
        # Condition 1: Missing Email or Phone -> Full Process
        if not data['email'] or not data['phone'] or data['email'].upper() == "NOT FOUND":
            # logger.info(f"Reprocessing {filename}: Missing Email/Phone.")
            should_full_process = True
        
        # Condition 2: Missing Hyperlink OR Broken Formula -> Update Link Only
        elif not data['is_hyperlink'] or data['needs_fix']:
            # logger.info(f"Updating Link for {filename}.")
            should_full_process = False
            use_existing_data = True
        
        # Condition 3: All Good -> Skip
        else:
            return {'action': 'SKIP', 'filename': filename}
    
    # If we are here, we either need full process or just update link
    
    if use_existing_data and existing_data:
        row_data = [
            filename_cell, 
            existing_data['email'], 
            existing_data['phone'],
            "Oui" if existing_data['email'] != "NOT FOUND" else "Non", # Status
            "", # JSON Link
            existing_data.get('language', '') # Language
        ]
        return {'action': 'UPDATE', 'row_index': row_index_to_update, 'data': row_data, 'filename': filename}

    if should_full_process:
        try:
            # DOWNLOAD FILE ON DEMAND
            # Use unique filename to avoid collision in threads
            temp_filename = f"{file_id}_{filename}"
            file_path = download_file(drive_service, file_id, temp_filename, TEMP_DIR)
            
            # Extract Text
            text = ""
            _, ext = os.path.splitext(filename)
            if ext.lower() == '.pdf':
                text, _ = extract_text_from_pdf(file_path)
            elif ext.lower() == '.docx':
                text = extract_text_from_docx(file_path)
            
            # Remove file after processing
            if os.path.exists(file_path):
                os.remove(file_path)
            
            if not text:
                logger.warning(f"Could not extract text from {filename}")
                row_data = [filename_cell, "NOT FOUND", "", "", "", "Unknown"]
                if row_index_to_update != -1:
                    return {'action': 'UPDATE', 'row_index': row_index_to_update, 'data': row_data, 'filename': filename}
                else:
                    return {'action': 'APPEND', 'data': row_data, 'filename': filename}

            # Detect Language
            language = detect_language(text)
            
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
            
            # Add Status="Oui" if email found, else "Non"
            status_val = "Oui" if email else "Non"
            row_data = [filename_cell, email_val, phone, status_val, "", language]
            
            if row_index_to_update != -1:
                return {'action': 'UPDATE', 'row_index': row_index_to_update, 'data': row_data, 'filename': filename}
            else:
                return {'action': 'APPEND', 'data': row_data, 'filename': filename}
                
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}")
            return {'action': 'ERROR', 'filename': filename, 'error': str(e)}

    return {'action': 'SKIP', 'filename': filename}

def process_folder(folder_id, sheet_id, sheet_name="Feuille 1"):
    """
    Downloads CVs from a Drive folder, extracts emails, and saves them to a Sheet.
    Uses Parallel Processing and Batch Writing.
    """
    # 1. Authenticate
    logger.info("Authenticating with Google Services...")
    drive_service = get_drive_service()
    sheets_service = get_sheets_service()

    # 2. Deduplicate existing data first
    deduplicate_sheet(sheets_service, sheet_id, sheet_name)
    
    # Load existing data
    existing_rows = get_sheet_values(sheets_service, sheet_id, sheet_name, value_render_option='FORMULA')
    existing_data_map = {}
    
    if existing_rows:
        for i, row in enumerate(existing_rows):
            if i == 0: continue # Skip header
            raw_filename = row[0] if len(row) > 0 else ""
            
            # Check if it's a hyperlink formula (English or French)
            is_hyperlink = raw_filename.startswith('=HYPERLINK') or raw_filename.startswith('=LIEN_HYPERTEXTE')
            
            # Check if it uses the correct French format
            is_correct_format = raw_filename.startswith('=LIEN_HYPERTEXTE') and ';' in raw_filename
            
            # Extract clean filename
            if is_hyperlink:
                # Match both formats: HYPERLINK("url", "name") or LIEN_HYPERTEXTE("url"; "name")
                match = re.search(r'"([^"]+)"\)$', raw_filename)
                clean_filename = match.group(1) if match else raw_filename
            else:
                clean_filename = raw_filename
            
            email = row[1] if len(row) > 1 else ""
            phone = row[2] if len(row) > 2 else ""
            language = row[5] if len(row) > 5 else ""
            
            # Extract File ID from HYPERLINK formula
            # Format: =HYPERLINK("https://drive.google.com/file/d/FILE_ID/view...", "name")
            # Regex for ID: /d/([a-zA-Z0-9_-]+)
            file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', raw_filename)
            file_id_from_excel = file_id_match.group(1) if file_id_match else ""
            
            if file_id_from_excel:
                existing_data_map[file_id_from_excel] = {
                    'index': i,
                    'email': str(email).strip(),
                    'phone': str(phone).strip(),
                    'language': str(language).strip(),
                    'is_hyperlink': is_hyperlink,
                    'needs_fix': is_hyperlink and not is_correct_format,
                    'status': str(row[3]).strip() if len(row) > 3 else ""
                }

    # 3. Create Temp Directory
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    try:
        # 5b. Create/Get Processed Folder
        processed_folder_id = get_or_create_folder(drive_service, "_processed", parent_id=folder_id)
        logger.info(f"Processed files will be moved to folder ID: {processed_folder_id}")

        # 4. List Files (Metadata only) - FROM SOURCE ONLY
        # Use server-side sorting and limiting for Source to avoid listing 10,000 files
        logger.info(f"Listing top 50 most recent files from Source Folder ID: {folder_id}")
        source_files = list_files_in_folder(drive_service, folder_id, order_by='modifiedTime desc', page_size=50)
        
        # Identify files needing update from Excel
        files_needing_update = []
        for fid, data in existing_data_map.items():
            # Check if needs update: Email/Phone missing/NOT FOUND, or Status="Non" (and not "Delete")
            # PRIORITIZE: Empty Status (Never processed)
            email = data['email'].upper()
            phone = data['phone']
            status = data['status'].upper()
            
            if status == "DELETE":
                continue
                
            needs_update = False
            priority = 0 # Higher is better
            
            if status == "":
                needs_update = True
                priority = 2 # Highest priority: Never processed
            elif email == "" or email == "NOT FOUND" or status == "NON":
                needs_update = True
                priority = 1 # Medium priority: Failed previously
                
            if needs_update:
                files_needing_update.append({'id': fid, 'priority': priority})
                
        # Sort updates by priority (descending), then we'll fetch metadata
        files_needing_update.sort(key=lambda x: x['priority'], reverse=True)
        
        # Extract just IDs
        update_ids = [x['id'] for x in files_needing_update]
                
        # Mark source files
        for f in source_files:
            f['is_processed'] = False
            
        # Combine: Source Files + Files Needing Update
        # We need to fetch metadata for update_ids if they are not in source_files
        source_ids = set(f['id'] for f in source_files)
        
        # Limit updates to top 25 to avoid explosion
        # But wait, if we have 100 empty status files, we want to do them ALL before new files?
        # The user said "Prioritize files... with no status defined".
        # So we should perhaps take them even before source_files?
        # Let's keep the mix but ensure high priority ones get in.
        
        update_ids = update_ids[:50]
        
        for fid in update_ids:
            if fid not in source_ids:
                try:
                    f = drive_service.files().get(fileId=fid, fields="id, name, webViewLink, modifiedTime", supportsAllDrives=True).execute()
                    f['is_processed'] = True # Mark as "processed" (conceptually, i.e. not new)
                    f['needs_move'] = False # Already moved presumably
                    f['priority'] = next((x['priority'] for x in files_needing_update if x['id'] == fid), 0)
                    
                    # Add to source_files? No, add to a separate list or extend
                    source_files.append(f)
                except Exception as e:
                    logger.warning(f"Could not fetch metadata for update candidate {fid}: {e}")

        all_files = source_files
        
        if not all_files:
            logger.warning("No files found in Drive (Source) and no updates needed.")
            return

        logger.info(f"Found {len(all_files)} candidates.")

        # --- PRE-FLIGHT CHECK: Move files already in Excel to _processed ---
        logger.info("Running Pre-flight Check: Moving files already in Excel to _processed...")
        files_to_process = []
        moved_file_ids = set()
        
        # Add already processed files (from Excel map) to moved_file_ids
        for file_data in all_files:
            filename = file_data['name']
            file_id = file_data['id']
            
            # Check by ID first
            if file_id in existing_data_map:
                if file_data.get('needs_move', True): # Default True for source files
                     try:
                        move_file(drive_service, file_id, folder_id, processed_folder_id)
                        file_data['is_processed'] = True
                        moved_file_ids.add(file_id)
                     except Exception as e:
                        logger.error(f"Pre-flight move failed for {filename}: {e}")
            else:
                # File is NOT in Excel -> Needs processing
                # Assign priority 0 (New file)
                file_data['priority'] = 0
                files_to_process.append(file_data)
                
        # Rebuild files_to_process to include updates
        files_to_process = []
        for file_data in all_files:
            file_id = file_data['id']
            # If not in Excel, it's new.
            if file_id not in existing_data_map:
                 file_data['priority'] = 0 # New files have base priority
                 files_to_process.append(file_data)
            else:
                # In Excel. Check if needs update.
                # We already calculated priority for these.
                # If it's in all_files, it was either in Source or in our update list.
                
                # If it was in Source but is also in Excel, we need to check if it needs update
                data = existing_data_map[file_id]
                email = data['email'].upper()
                status = data['status'].upper()
                
                priority = 0
                if status == "DELETE":
                    continue
                elif status == "":
                    priority = 2
                elif email == "" or email == "NOT FOUND" or status == "NON":
                    priority = 1
                    
                if priority > 0:
                    file_data['priority'] = priority
                    files_to_process.append(file_data)
        
        # Sort by Priority (Desc), then ModifiedTime (Desc)
        # Priority 2 (Empty Status) > Priority 1 (Retry) > Priority 0 (New)
        files_to_process.sort(key=lambda x: (x.get('priority', 0), x.get('modifiedTime', '')), reverse=True)
        
        # Take top 50
        files_to_process = files_to_process[:50]
        logger.info(f"Selected top {len(files_to_process)} files for processing (Prioritizing Empty Status).")
        
        # 5. Process Files in Parallel
        append_buffer = []
        update_buffer = []
        BATCH_SIZE = 50
        MAX_WORKERS = 5
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit tasks
            future_to_file = {
                executor.submit(process_single_file, file_data, existing_data_map): file_data 
                for file_data in files_to_process
            }
            
            processed_count = 0
            for future in as_completed(future_to_file):
                file_data = future_to_file[future]
                file_id = file_data['id']
                result = future.result()
                processed_count += 1
                
                should_move = False
                
                if result['action'] == 'APPEND':
                    append_buffer.append(result['data'])
                    logger.info(f"[{processed_count}/{len(all_files)}] Processed (New): {result['filename']}")
                    should_move = True
                elif result['action'] == 'UPDATE':
                    update_buffer.append((result['row_index'], result['data']))
                    logger.info(f"[{processed_count}/{len(all_files)}] Processed (Update): {result['filename']}")
                    should_move = True
                elif result['action'] == 'SKIP':
                    # logger.info(f"[{processed_count}/{len(all_files)}] Skipped (Already Valid): {result['filename']}")
                    should_move = True
                elif result['action'] == 'ERROR':
                    logger.error(f"Failed: {result['filename']} - {result.get('error')}")
                # Move to _processed if successful or skipped AND NOT ALREADY PROCESSED
                # Check global moved_file_ids set to prevent double moves
                
                if should_move and file_id not in moved_file_ids:
                    try:
                        move_file(drive_service, file_id, folder_id, processed_folder_id)
                        moved_file_ids.add(file_id)
                    except Exception as e:
                        logger.error(f"Failed to move {result['filename']}: {e}")

                # Batch Write
                if len(append_buffer) >= BATCH_SIZE:
                    logger.info(f"Flushing {len(append_buffer)} new rows to Sheet...")
                    append_batch_to_sheet(sheets_service, sheet_id, append_buffer, sheet_name)
                    append_buffer = []

                if len(update_buffer) >= BATCH_SIZE:
                    logger.info(f"Flushing {len(update_buffer)} updates to Sheet...")
                    batch_update_rows(sheets_service, sheet_id, update_buffer, sheet_name)
                    update_buffer = []
        
        # Flush remaining
        if append_buffer:
            logger.info(f"Flushing remaining {len(append_buffer)} new rows...")
            append_batch_to_sheet(sheets_service, sheet_id, append_buffer, sheet_name)
            
        if update_buffer:
            logger.info(f"Flushing remaining {len(update_buffer)} updates...")
            batch_update_rows(sheets_service, sheet_id, update_buffer, sheet_name)

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
