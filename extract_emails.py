import os
import argparse
import shutil
import logging
from google_drive import (
    get_drive_service, get_sheets_service, list_files_in_folder, 
    download_file, append_to_sheet, get_sheet_values, 
    clear_and_write_sheet, format_header_row, update_sheet_row,
    append_batch_to_sheet, batch_update_rows, set_column_validation,
    get_or_create_folder, move_file, delete_rows, upload_file_to_folder
)
import re
import difflib
from simple_parsers import extract_text_from_pdf, extract_text_from_docx, heuristic_parse_contact
from ai_parsers import parse_cv_full_text
from report_generator import format_candidate_row
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TEMP_DIR = "temp_cvs"
INDEX_DIR = "index_cvs"
JSON_DIR = "output_jsons"
INDEXED_COL_IDX = 6 # Column G (0-based index)

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

def create_hyperlink_formula(url, text):
    """Creates a Google Sheets HYPERLINK formula (French format)."""
    # Escape double quotes in text
    text = text.replace('"', '""')
    return f'=LIEN_HYPERTEXTE("{url}"; "{text}")'

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
    
    expected_header = ["Filename", "Email", "Phone", "Status", "Emplacement", "Language", "Lien Index"]
    
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

def process_single_file(file_data, existing_data_map, source_folder_id, processed_folder_id, index_folder_id):
    """
    Processes a single file: checks if it needs processing, downloads, extracts info.
    Returns a dict with action ('APPEND', 'UPDATE', 'SKIP') and data.
    """
    # Create a thread-local Drive service to avoid SSL/Memory corruption
    drive_service = get_drive_service()
    
    file_id = file_data['id']
    clean_filename = file_data['name']
    file_id = file_data['id']
    file_link = file_data.get('webViewLink') or file_data.get('link')
    
    # CRITICAL FIX: Ensure we always have a link. 
    # If webViewLink is missing, construct it manually from ID.
    if not file_link and file_id:
        file_link = f"https://drive.google.com/file/d/{file_id}/view"
        logger.warning(f"webViewLink missing for {clean_filename} ({file_id}). Constructed manual link.")

    # Construct Hyperlink Formula for Filename
    # =HYPERLINK("link", "name")
    if file_link:
        # Use helper function
        filename_cell = create_hyperlink_formula(file_link, clean_filename)
    else:
        # This should rarely happen now with the fallback
        logger.error(f"Could not generate link for {clean_filename}. Excel link will be broken.")
        filename_cell = clean_filename
        
    md_link = "" # Initialize md_link

    # Check if we have existing data for this file
    use_existing_data = False
    existing_data = None
    should_full_process = True # Default to True for new files
    row_index_to_update = -1 # Default to -1 (Append mode)
    
    # Use File ID as key
    if file_id in existing_data_map:
        data = existing_data_map[file_id]
        row_index_to_update = data['index']
        existing_data = data
        
        # Condition 1: Status "NON" (Email NOT FOUND) -> FORCE FULL PROCESS
        # User Request: Retry extraction for these files to regenerate MD.
        if data['status'].upper() == "NON" or data['email'].upper() == "NOT FOUND":
             should_full_process = True
             logger.info(f"Force Reprocessing for {clean_filename} (Status: NON/NOT FOUND)")

        # Condition 1.5: Missing Language -> Full Process (to detect language)
        elif not data.get('language'):
             should_full_process = True
        
        elif not data.get('is_indexed'):
             should_full_process = True
        
        # Condition 2: Missing Hyperlink OR Broken Formula -> Update Link Only
        elif not data['is_hyperlink'] or data['needs_fix']:
            # logger.info(f"Updating Link for {filename}.")
            should_full_process = False
            use_existing_data = True
        
        # Condition 3: All Good -> Skip
        else:
            return {'action': 'SKIP', 'filename': clean_filename}
    
    # If we are here, we either need full process or just update link
    
    if use_existing_data and existing_data:
        row_data = [
            filename_cell, 
            existing_data['email'], 
            existing_data['phone'],
            "Oui" if existing_data['email'] != "NOT FOUND" else "Non", # Status
            "Processed", # Emplacement (Assume Processed if we are updating link, or should we check?)
            existing_data.get('language', '') # Language
        ]
        # If we are just updating link, we might want to verify location? 
        # But for simplicity, if it's in the sheet, we assume it's processed or will be.
        # Actually, let's check the file_data parents if available.
        parents = file_data.get('parents', [])
        location = "Processed"
        if source_folder_id in parents: location = "CVS"
        elif processed_folder_id in parents: location = "Processed"
        
        row_data[4] = location
        return {'action': 'UPDATE', 'row_index': row_index_to_update, 'data': row_data, 'filename': clean_filename, 'is_indexed': True, 'md_link': md_link}

    if should_full_process:
        try:
            # DOWNLOAD FILE ON DEMAND
            # Use unique filename to avoid collision in threads
            temp_filename = f"{file_id}_{clean_filename}"
            file_path = download_file(drive_service, file_id, temp_filename, TEMP_DIR)
            
            # Extract Text
            text = ""
            _, ext = os.path.splitext(clean_filename)
            if ext.lower() == '.pdf':
                text, _ = extract_text_from_pdf(file_path)
            elif ext.lower() == '.docx':
                text = extract_text_from_docx(file_path)
            
            # Remove file after processing
            if os.path.exists(file_path):
                os.remove(file_path)
            
            if not text:
                logger.warning(f"Could not extract text from {clean_filename}")
                row_data = [filename_cell, "NOT FOUND", "", "", "CVS", "Unknown"]
                if row_index_to_update != -1:
                    return {'action': 'UPDATE', 'row_index': row_index_to_update, 'data': row_data, 'filename': clean_filename}
                else:
                    return {'action': 'APPEND', 'data': row_data, 'filename': clean_filename}

            # Detect Language
            language = detect_language(text)
            
            # Extract Email
            # Extract Email
            # Scan FULL text, not just the head, to avoid missing emails at the bottom
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = list(set(re.findall(email_pattern, text)))
            email = select_best_email(emails, clean_filename)
            
            # Extract other info
            contact_info = heuristic_parse_contact(text)
            phone = contact_info.get('phone', '')
            
            # Clean Phone
            phone = clean_phone_number(phone)
            
            # Prepare Row Data
            email_val = email if email else "NOT FOUND"
            phone_val = phone
            
            # Status is now "Oui" (Touched) for ANY processed file
            status_val = "Oui" 
            
            # PRESERVE EXISTING DATA if available and valid
            if existing_data:
                # If we have an existing valid email, keep it
                if existing_data['email'] and existing_data['email'] != "NOT FOUND":
                    email_val = existing_data['email']
                    # status_val = existing_data['status'] # REMOVED: Always set to Oui (Touched)
                
                # If we have an existing phone, keep it
                if existing_data['phone']:
                    phone_val = existing_data['phone']

            # We are about to move it to Processed, so write "Processed"
            row_data = [filename_cell, email_val, phone_val, status_val, "Processed", language]
            
            # --- START INDEXING (MARKDOWN) ---
            try:
                # Create Index Directory if not exists
                if not os.path.exists(INDEX_DIR):
                    os.makedirs(INDEX_DIR)
                    
                # Prepare Metadata for Frontmatter
                # Escape quotes for YAML
                def escape_yaml(s):
                    return str(s).replace('"', '\\"')
                
                md_filename = f"{file_id}.md"
                md_path = os.path.join(INDEX_DIR, md_filename)
                
                # Create Markdown Content
                md_content = f"""---
id: "{file_id}"
filename: "{escape_yaml(clean_filename)}"
email: "{escape_yaml(email_val)}"
phone: "{escape_yaml(phone_val)}"
language: "{escape_yaml(language)}"
url: "{file_link}"
date_processed: "{os.environ.get('GITHUB_RUN_ID', 'local')}"
---

# {clean_filename}

{text}
"""
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(md_content)
                    
                # Upload to Drive (_index_cvs) and get Link
                # We need the index_folder_id. It's passed via process_single_file args.
                # We need to return the link.
                # Note: upload_file_to_folder returns (id, link)
                # But we are doing the upload in the main thread (process_folder) loop?
                # No, process_single_file runs in thread.
                # We can't upload here easily if we don't have the service object for this thread?
                # We DO have a thread-local drive_service created at start of process_single_file.
                
                # Wait, upload_file_to_folder was moved to main loop in previous edit?
                # Let's check the code.
                # In previous edit, I added `if 'md_path' in result... upload...` in the main loop.
                # So process_single_file just creates the file and returns the path.
                # The main loop does the upload.
                # So we need the main loop to get the link and pass it back.
                # For now, we return md_path, and the main loop will handle upload and link generation.
                # The instruction to return md_link from here is contradictory to the current design.
                # Sticking to returning md_path for now, as per the existing logic.
                # The main loop will then update the row with the actual md_link.
                pass 
            except Exception as e:
                logger.error(f"Error creating index for {clean_filename}: {e}")
                # Don't fail the whole process for indexing error
            # --- END INDEXING ---

            # --- START JSON EXTRACTION & REPORTING ---
            # REMOVED: JSON extraction is now handled by Pipeline 2 (etl_extract.py)
            # to avoid unnecessary AI calls and credit usage in Pipeline 1.
            json_data = {}
            # --- END JSON EXTRACTION ---

            if row_index_to_update != -1:
                return {'action': 'UPDATE', 'row_index': row_index_to_update, 'data': row_data, 'filename': clean_filename, 'md_path': md_path, 'is_indexed': True, 'md_link': md_link, 'json_data': json_data}
            else:
                return {'action': 'APPEND', 'data': row_data, 'filename': clean_filename, 'md_path': md_path, 'is_indexed': True, 'md_link': md_link, 'json_data': json_data}
                
        except Exception as e:
            logger.error(f"Error processing {clean_filename}: {e}")
            return {'action': 'ERROR', 'filename': clean_filename, 'error': str(e), 'md_link': md_link}

    return {'action': 'SKIP', 'filename': clean_filename}

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
    missing_id_map = {} # Store rows with missing IDs (Broken Links)
    
    if existing_rows:

        # --- STATUS AGING (User Request) ---
        # "Oui" -> "Oui -1"
        # "Oui -N" -> "Oui -(N+1)"
        # "Non" remains "Non"
        logger.info("Aging Status column (Oui -> Oui -1)...")
        status_updates = []
        for i, row in enumerate(existing_rows):
            if i == 0: continue # Skip header
            
            current_status = str(row[3]).strip() if len(row) > 3 else ""
            
            if current_status.upper() == "DELETE":
                continue
            
            new_status = current_status
            
            if current_status == "Oui":
                new_status = "Oui -1"
            elif current_status.startswith("Oui -"):
                try:
                    # Extract number
                    parts = current_status.split('-')
                    if len(parts) == 2:
                        num = int(parts[1].strip())
                        new_status = f"Oui -{num + 1}"
                except:
                    pass # Keep original if parse fails
            
            # Only update if changed
            if new_status != current_status:
                status_updates.append({
                    'range': f"'{sheet_name}'!D{i+1}",
                    'values': [[new_status]]
                })
        
        if status_updates:
            # Batch update in chunks of 500
            chunk_size = 500
            for k in range(0, len(status_updates), chunk_size):
                chunk = status_updates[k:k+chunk_size]
                body = {'data': chunk, 'valueInputOption': 'USER_ENTERED'}
                try:
                    sheets_service.spreadsheets().values().batchUpdate(
                        spreadsheetId=sheet_id, body=body
                    ).execute()
                except Exception as e:
                    logger.error(f"Error aging status: {e}")
            logger.info(f"Aged {len(status_updates)} rows.")
        # --- END AGING ---
        
        # Re-iterate for main processing
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
                    'is_hyperlink': is_hyperlink,
                    'needs_fix': is_hyperlink and not is_correct_format,
                    'status': str(row[3]).strip() if len(row) > 3 else "",
                    'is_indexed': len(row) > 6 and str(row[6]).strip() != "" # Check Column G (Index 6)
                }
            elif clean_filename:
                # Fallback: Map by Filename if ID is missing (Broken Link)
                # We prefix with "NAME:" to distinguish from IDs if needed, or just check format.
                # Actually, let's put it in the same map but we need to know it's a name.
                # But existing_data_map expects ID as key.
                # Let's use a separate map for these "orphans".
                pass # We'll handle this in a second pass below? 
                # No, let's store it in existing_data_map with a special key or just the filename?
                # If we store by filename, we can't look it up by ID later.
                # So we need a separate list of "rows to fix by name".
                
                # Let's store it in a separate dict
                missing_id_map[clean_filename] = {
                    'index': i,
                    'email': str(email).strip(),
                    'phone': str(phone).strip(),
                    'language': str(language).strip(),
                    'is_hyperlink': False, # It's text
                    'needs_fix': True,
                    'status': str(row[3]).strip() if len(row) > 3 else ""
                }

    # 3. Create Temp Directory
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    try:
        # 5b. Create/Get Processed Folder
        processed_folder_id = get_or_create_folder(drive_service, "_processed", parent_id=folder_id)
        logger.info(f"Processed files will be moved to folder ID: {processed_folder_id}")

        # 5c. Create/Get Index Folder
        # User requested a NEW folder to avoid "invisible" old folder issues.
        index_folder_id = get_or_create_folder(drive_service, "_cv_index_v2", parent_id=folder_id)
        logger.info(f"Index files will be uploaded to NEW folder ID: {index_folder_id} (Name: _cv_index_v2)")

        # 4. List Files (Metadata only) - FROM SOURCE ONLY
        logger.info(f"Listing top 100 most recent files from Source Folder ID: {folder_id}")
        source_files = list_files_in_folder(drive_service, folder_id, order_by='modifiedTime desc', page_size=100)
        
        # Identify files needing update from Excel
        files_needing_update = []
        
        # A. Check files WITH IDs
        for fid, data in existing_data_map.items():
            email = data['email'].upper()
            status = data['status'].upper()
            
            if status == "DELETE":
                continue
            
            # User Request: If Email is "OK", skip (manually marked as no email)
            if email == "OK":
                continue
                
            needs_update = False
            priority = 0
            
            if not data['is_hyperlink']:
                needs_update = True
                priority = 3
            
            # Priority 40: Failed Extraction (Email NOT FOUND or Empty) OR Status NON
            elif status == "NON" or email == "" or email == "NOT FOUND":
                needs_update = True
                priority = 40 # TOP PRIORITY
            
            elif status == "":
                # needs_update = True
                # priority = 20 
                pass # Empty status no longer triggers high priority update by default?
                # Actually, if status is empty, we probably SHOULD update it, but user was specific about priority 2.
                # Let's set needs_update=True but low priority if we want to fill it.
                needs_update = True
                priority = 1
            
            elif not data.get('language'):
                needs_update = True
                priority = 1
            
            elif not data.get('is_indexed'):
                needs_update = True
                priority = 10 # Indexing (Third Priority)
                
            if needs_update:
                files_needing_update.append({'id': fid, 'priority': priority})

        # B. Check files WITHOUT IDs (Broken Links) - Search by Name
        if missing_id_map:
            logger.info(f"Found {len(missing_id_map)} rows with missing IDs (Broken Links). Searching Drive...")
            for name, data in missing_id_map.items():
                # Search for this file in Source OR Processed
                # q = "name = 'NAME' and trashed = false"
                # We need to be careful about quotes in names
                safe_name = name.replace("'", "\\'")
                query = f"name = '{safe_name}' and trashed = false"
                
                try:
                    # Search in both folders? Or just globally?
                    # Globally is safer to find it wherever it is.
                    # But we should restrict to our folders if possible? 
                    # No, let's find it anywhere and check parents later if needed.
                    # Actually, just finding it is enough to get the ID.
                    
                    results = drive_service.files().list(
                        q=query,
                        fields="files(id, name, webViewLink, modifiedTime, parents)",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        pageSize=1
                    ).execute()
                    
                    files = results.get('files', [])
                    if files:
                        f = files[0]
                        fid = f['id']
                        logger.info(f"Found missing ID for '{name}': {fid}")
                        
                        # Add to existing_data_map so process_single_file can find the row index!
                        existing_data_map[fid] = data
                        
                        # Add to update queue with High Priority
                        files_needing_update.append({'id': fid, 'priority': 3})
                    else:
                        logger.warning(f"Could not find file '{name}' in Drive.")
                        
                except Exception as e:
                    logger.warning(f"Error searching for '{name}': {e}")

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
        
        update_ids = update_ids[:100]
        
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
                 file_data['priority'] = 30 # New files have HIGHEST priority (User Request)
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
                elif status == "NON":
                    priority = 40 # Status "NON" (TOP PRIORITY - User Request)
                elif status == "":
                    # priority = 1 # Empty status is now low priority or handled elsewhere?
                    # User didn't specify what to do with empty status now. 
                    # Let's leave it as default (0) or low priority.
                    pass
                elif email == "" or email == "NOT FOUND":
                    # priority = 1
                    pass
                
                elif not data.get('language'):
                    priority = 1
                
                elif not data.get('is_indexed'):
                    priority = 20 # Indexing (Second Priority)
                    
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
        indexed_buffer = [] # Buffer for "Indexé" column updates
        report_buffer = [] # Buffer for the new Detailed Report Sheet
        BATCH_SIZE = 50
        MAX_WORKERS = 5
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit tasks
            future_to_file = {
                executor.submit(process_single_file, file_data, existing_data_map, folder_id, processed_folder_id, index_folder_id): file_data 
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

                # Upload MD Index if available
                md_link = ""
                if 'md_path' in result and result['md_path']:
                    try:
                        _, md_link = upload_file_to_folder(drive_service, result['md_path'], index_folder_id, mime_type='text/markdown')
                        # Clean up local MD file
                        if os.path.exists(result['md_path']):
                            os.remove(result['md_path'])
                    except Exception as e:
                        logger.error(f"Failed to upload index for {result['filename']}: {e}")

                # Collect Index Updates
                if md_link:
                    # Create Hyperlink Formula
                    # =LIEN_HYPERTEXTE("url"; "name.md")
                    md_filename = os.path.basename(result['md_path']) if 'md_path' in result else f"{file_id}.md"
                    formula = create_hyperlink_formula(md_link, md_filename)
                    
                    if result['action'] == 'UPDATE':
                        indexed_buffer.append((result['row_index'], [formula]))
                    elif result['action'] == 'APPEND':
                        # For APPEND, we need to put it in Column G (Index 6).
                        # Current row_data length is 6 (Filename...Language).
                        # So we just append it.
                        # [Filename, Email, Phone, Status, Emplacement, Language, Lien Index]
                        result['data'].append(formula)
                        
                # Batch Write
                if len(append_buffer) >= BATCH_SIZE:
                    logger.info(f"Flushing {len(append_buffer)} new rows to Sheet...")
                    # Note: append_batch_to_sheet writes to A:Z now.
                    append_batch_to_sheet(sheets_service, sheet_id, append_buffer, sheet_name)
                    append_buffer = []

                if len(update_buffer) >= BATCH_SIZE:
                    logger.info(f"Flushing {len(update_buffer)} updates to Sheet...")
                    batch_update_rows(sheets_service, sheet_id, update_buffer, sheet_name)
                    update_buffer = []
                    
                if len(indexed_buffer) >= BATCH_SIZE:
                    logger.info(f"Flushing {len(indexed_buffer)} index status updates...")
                    batch_update_rows(sheets_service, sheet_id, indexed_buffer, sheet_name, start_col='G')
                    indexed_buffer = []
        
        # Flush remaining
        if append_buffer:
            logger.info(f"Flushing remaining {len(append_buffer)} new rows...")
            append_batch_to_sheet(sheets_service, sheet_id, append_buffer, sheet_name)
            
        if update_buffer:
            logger.info(f"Flushing remaining {len(update_buffer)} updates...")
            batch_update_rows(sheets_service, sheet_id, update_buffer, sheet_name)
            
        if indexed_buffer:
            logger.info(f"Flushing remaining {len(indexed_buffer)} index status updates...")
            batch_update_rows(sheets_service, sheet_id, indexed_buffer, sheet_name, start_col='G')

    finally:
        # 6. Cleanup
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
            logger.info("Temporary directory cleaned up.")
            
        if os.path.exists(INDEX_DIR):
            shutil.rmtree(INDEX_DIR)
            logger.info("Index directory cleaned up.")
            
        if os.path.exists(JSON_DIR):
            shutil.rmtree(JSON_DIR)
            logger.info("JSON directory cleaned up.")
            
        # 7. Set Data Validation for Status Column (Column D, index 3)
        logger.info("Setting data validation for Status column...")
        set_column_validation(sheets_service, sheet_id, sheet_name, 3, ["Oui", "Non", "Delete"])
        
        # 8. FINAL AUDIT & REPAIR
        # 8. FINAL AUDIT & REPAIR
        # We pass both source and processed folder IDs to search everywhere
        audit_and_repair_hyperlinks(drive_service, sheets_service, sheet_id, sheet_name, folder_id, processed_folder_id)
        
def create_hyperlink_formula(url, name):
    """
    Generates a valid French Excel Hyperlink formula.
    Format: =LIEN_HYPERTEXTE("url"; "name")
    """
    # Escape double quotes in name if necessary
    safe_name = name.replace('"', '""')
    return f'=LIEN_HYPERTEXTE("{url}"; "{safe_name}")'

def build_file_cache(drive_service, folder_ids):
    """
    Fetches ALL files from the specified folders to build a local cache.
    Returns:
        id_map: {file_id: file_metadata}
        name_map: {filename: file_metadata} (Prioritizes most recent if duplicates)
    """
    logger.info(f"Building file cache from folders: {folder_ids}...")
    id_map = {}
    name_map = {}
    
    for folder_id in folder_ids:
        page_token = None
        while True:
            try:
                # We need name, id, webViewLink, parents
                query = f"'{folder_id}' in parents and trashed = false"
                results = drive_service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name, webViewLink, parents, modifiedTime)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000, # Maximize page size for speed
                    pageToken=page_token
                ).execute()
                
                files = results.get('files', [])
                for f in files:
                    id_map[f['id']] = f
                    # For name map, if duplicate, maybe keep the one in 'processed' or most recent?
                    # Let's just overwrite for now, or check modifiedTime.
                    # Ideally we want the one that matches the CV being processed.
                    name_map[f['name']] = f
                    
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
            except Exception as e:
                logger.error(f"Error listing files in folder {folder_id}: {e}")
                break
                
    logger.info(f"Cache built: {len(id_map)} files found.")
    return id_map, name_map

def normalize_string(s):
    """Normalizes string for fuzzy matching (lower, strip, remove accents/extensions)."""
    if not s: return ""
    s = s.lower().strip()
    # Remove common extensions
    s = re.sub(r'\.(pdf|docx|doc)$', '', s)
    # Remove accents (simple way)
    import unicodedata
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    return s

def audit_and_repair_hyperlinks(drive_service, sheets_service, spreadsheet_id, sheet_name, source_folder_id, processed_folder_id):
    """
    Scans the sheet to:
    1. Update 'Emplacement' (Column E) based on actual file location (CVS vs Processed).
    2. Repair broken/invalid hyperlinks in Column A.
    """
    logger.info("Running Audit & Repair (Location + Hyperlinks)...")
    
    # 1. Build Cache
    folder_ids = [source_folder_id]
    if processed_folder_id:
        folder_ids.append(processed_folder_id)
        
    id_map, name_map = build_file_cache(drive_service, folder_ids)
    
    # Build a normalized name map for fuzzy search
    fuzzy_map = {}
    for name, meta in name_map.items():
        norm = normalize_string(name)
        fuzzy_map[norm] = meta

    rows = get_sheet_values(sheets_service, spreadsheet_id, sheet_name, value_render_option='FORMULA')
    if not rows:
        return

    updates = []
    
    for i, row in enumerate(rows):
        if i == 0: continue # Skip header
        
        filename_cell = row[0] if len(row) > 0 else ""
        current_location = row[4] if len(row) > 4 else ""
        
        # --- 1. Identify File ---
        found_file = None
        
        # A. Try ID from Formula
        id_match = re.search(r'/d/([a-zA-Z0-9_-]+)|id=([a-zA-Z0-9_-]+)', filename_cell)
        if id_match:
            extracted_id = id_match.group(1) or id_match.group(2)
            if extracted_id in id_map:
                found_file = id_map[extracted_id]
        
        # B. Try Name (Exact or Fuzzy)
        if not found_file:
            clean_name = filename_cell
            if filename_cell.startswith('='):
                 name_match = re.search(r';\s*"([^"]+)"\)$', filename_cell)
                 if name_match:
                     clean_name = name_match.group(1)
            
            if clean_name in name_map:
                found_file = name_map[clean_name]
            else:
                norm = normalize_string(clean_name)
                if norm in fuzzy_map:
                    found_file = fuzzy_map[norm]

        # --- 2. Determine Updates ---
        needs_update = False
        new_row = list(row)
        # Ensure row has enough columns (up to Index 5 for Language)
        while len(new_row) < 6:
            new_row.append("")
            
        # A. Update Location
        if found_file:
            parents = found_file.get('parents', [])
            actual_location = "Autre"
            if source_folder_id in parents:
                actual_location = "CVS"
            elif processed_folder_id in parents:
                actual_location = "Processed"
            
            if current_location != actual_location:
                new_row[4] = actual_location
                needs_update = True
                # logger.info(f"Row {i+1}: Updating Location '{current_location}' -> '{actual_location}'")

        # B. Repair Link (if invalid)
        is_valid_formula = (filename_cell.startswith('=LIEN_HYPERTEXTE') or filename_cell.startswith('=HYPERLINK')) and ';' in filename_cell
        
        if (not is_valid_formula) and found_file:
             file_link = found_file.get('webViewLink') or f"https://drive.google.com/file/d/{found_file['id']}/view"
             new_formula = create_hyperlink_formula(file_link, found_file['name'])
             
             if new_row[0] != new_formula:
                 new_row[0] = new_formula
                 needs_update = True
                 logger.info(f"Row {i+1}: Repaired Link -> {found_file['name']}")

        if needs_update:
            updates.append((i, new_row))
            
    if updates:
        logger.info(f"Flushing {len(updates)} repairs/updates to Sheet...")
        batch_update_rows(sheets_service, spreadsheet_id, updates, sheet_name)
    else:
        logger.info("No updates needed.")

def extract_file_id_from_hyperlink_formula(formula):
    """
    Extracts Google Drive File ID from a HYPERLINK formula.
    Supports:
    - English: =HYPERLINK("url", "name")
    - French: =LIEN_HYPERTEXTE("url"; "name")
    - Separators: comma (,) or semicolon (;)
    """
    if not formula:
        return None
        
    # Regex to find the URL part: "https://drive.google.com/..."
    # We look for /d/FILE_ID
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', formula)
    if match:
        return match.group(1)
    return None

def build_row_index_from_sheet_rows(rows):
    """
    Builds a map of {file_id: row_index} from sheet rows.
    Returns:
        index_map: {file_id: row_index}
        data_map: {file_id: {full_row_data_dict}}
    """
    index_map = {}
    data_map = {}
    
    if not rows:
        return index_map, data_map
        
    for i, row in enumerate(rows):
        if i == 0: continue # Skip header
        
        raw_filename = row[0] if len(row) > 0 else ""
        file_id = extract_file_id_from_hyperlink_formula(raw_filename)
        
        if file_id:
            index_map[file_id] = i
            
            # Extract other metadata
            is_hyperlink = raw_filename.startswith('=')
            is_correct_format = is_hyperlink and (
                (raw_filename.startswith('=LIEN_HYPERTEXTE') and ';' in raw_filename) or 
                (raw_filename.startswith('=HYPERLINK') and ',' in raw_filename)
            )
            
            email = row[1] if len(row) > 1 else ""
            phone = row[2] if len(row) > 2 else ""
            status = row[3] if len(row) > 3 else ""
            language = row[5] if len(row) > 5 else ""
            
            data_map[file_id] = {
                'index': i,
                'email': str(email).strip(),
                'phone': str(phone).strip(),
                'language': str(language).strip(),
                'is_hyperlink': is_hyperlink,
                'needs_fix': is_hyperlink and not is_correct_format,
                'status': str(status).strip()
            }
            
    return index_map, data_map

def ensure_report_headers(service, sheet_id, sheet_name):
    """
    Checks if the report sheet exists and has headers. 
    If it doesn't exist, creates it and writes headers.
    If it exists but is empty, writes headers.
    """
    logger.info(f"Checking headers for sheet '{sheet_name}'...")
    
    headers = [
        "Prénom", "Nom", "Email", "Téléphone", "Adresse", 
        "Langues", "Années Expérience", "Dernier Titre", 
        "Dernière Localisation", "Lien MD"
    ]
    
    try:
        # Check first row
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1:J1"
        ).execute()
        values = result.get('values', [])
        
        if not values:
            logger.info(f"Sheet '{sheet_name}' exists but is empty. Writing headers...")
            body = {'values': [headers]}
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            logger.info("Headers written successfully.")
        else:
            logger.info(f"Sheet '{sheet_name}' already has headers.")
            
    except Exception as e:
        # If error is likely "Sheet not found"
        logger.warning(f"Sheet '{sheet_name}' not found or inaccessible ({e}). Attempting to create it...")
        try:
            body = {
                'requests': [{
                    'addSheet': {
                        'properties': {
                            'title': sheet_name
                        }
                    }
                }]
            }
            service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
            logger.info(f"Created new sheet '{sheet_name}'.")
            
            # Now write headers
            body = {'values': [headers]}
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{sheet_name}'!A1",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            logger.info("Headers written to new sheet.")
            
        except Exception as create_error:
            logger.error(f"Failed to create sheet '{sheet_name}': {create_error}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract emails from CVs in a Google Drive folder.")
    parser.add_argument("--folder_id", required=True, help="Google Drive Folder ID containing CVs")
    parser.add_argument("--sheet_id", required=True, help="Google Sheet ID to save results")
    parser.add_argument("--sheet_name", default="Feuille 1", help="Name of the sheet to write to (default: 'Feuille 1')")
    
    args = parser.parse_args()
    
    process_folder(args.folder_id, args.sheet_id, args.sheet_name)
