import os
import json
import logging
import io
import yaml
import re
import concurrent.futures
from dotenv import load_dotenv
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google_drive import (
    get_drive_service, list_files_in_folder, download_file, 
    upload_file_to_folder, get_or_create_folder, move_file,
    get_sheets_service, append_batch_to_sheet, upsert_batch_to_sheet, ensure_report_headers,
    remove_empty_rows, remove_duplicates_by_column, create_hyperlink_formula, get_sheet_values
)
from parsers import parse_cv_from_text
from report_generator import format_candidate_row

# Configuration
DOWNLOADS_DIR = "downloads"
JSON_OUTPUT_DIR = "output_jsons"

def process_file_by_id(file_id, cv_link, json_output_folder_id, index=0, total=0, languages_source=""):
    """
    Process a single MD file by ID in a separate thread.
    Fetches metadata, downloads, extracts, and returns report row.
    """
    # Create thread-local service
    try:
        drive_service = get_drive_service()
    except Exception as e:
        logger.error(f"Thread failed to auth for {file_id}: {e}")
        return False, None, {'id': file_id, 'name': 'Unknown'}

    # Fetch File Metadata
    try:
        file_item = drive_service.files().get(fileId=file_id, fields='id, name').execute()
        file_name = file_item['name']
    except Exception as e:
        logger.error(f"Failed to fetch metadata for {file_id}: {e}")
        return False, None, {'id': file_id, 'name': 'Unknown'}

    logger.info(f"[{index}/{total}] Processing file (Thread): {file_name} ({file_id})")
    
    try:
        # 1. Download MD File
        local_path = download_file(drive_service, file_id, file_name, DOWNLOADS_DIR)
        if not local_path:
            logger.error(f"Failed to download {file_name}")
            return False, None, file_item

        # 2. Read Content
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # 3. Parse Frontmatter
        metadata = {}
        body_text = content
        
        if content.startswith("---"):
            try:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    body_text = parts[2]
                    metadata = yaml.safe_load(frontmatter)
            except Exception as e:
                logger.warning(f"Failed to parse frontmatter for {file_name}: {e}")
        
        # 4. Parse (AI Extraction)
        parsed_data = parse_cv_from_text(body_text, file_name, metadata=metadata)
        
        if not parsed_data:
            logger.error(f"Failed to parse {file_name}")
            return False, None, file_item

        # 5. Save JSON Locally
        base_name = os.path.splitext(file_name)[0]
        json_filename = f"{base_name}_extracted.json"
        if not os.path.exists(JSON_OUTPUT_DIR):
            os.makedirs(JSON_OUTPUT_DIR)
        json_output_path = os.path.join(JSON_OUTPUT_DIR, json_filename)
        
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
        # 6. Upload JSON to Drive
        json_file_id, json_link = upload_file_to_folder(drive_service, json_output_path, json_output_folder_id)
        
        logger.info(f"SUCCESS: Extracted {file_name} -> {json_filename} ({json_link})")
        
        # 7. Generate Report Row
        raw_link = f"https://drive.google.com/file/d/{file_id}/view"
        md_link = create_hyperlink_formula(raw_link, file_name)
        
        # Generate JSON Link
        if not json_link:
             json_link = f"https://drive.google.com/file/d/{json_file_id}/view"
        json_link_formula = create_hyperlink_formula(json_link, "Voir JSON")

        try:
            report_row = format_candidate_row(
                parsed_data, 
                md_link, 
                emplacement=languages_source, # Use the source language value
                json_link=json_link_formula,
                cv_link=cv_link
            )
            return True, report_row, file_item
        except Exception as e:
            logger.error(f"Failed to generate report row for {file_name}: {e}")
            return False, None, file_item

    except Exception as e:
        logger.error(f"Error processing {file_name}: {e}", exc_info=True)
        return False, None, file_item

def process_file(file_item, drive_service, output_folder_id, report_buffer):
    """
    Process a single MD file: Read Content -> Parse -> Upload JSON -> Generate Report Row
    """
    file_id = file_item['id']
    file_name = file_item['name']
    
    logger.info(f"Processing file: {file_name} ({file_id})")
    
    try:
        # 1. Download MD File (or read content directly if small)
        local_path = download_file(drive_service, file_id, file_name, DOWNLOADS_DIR)
        if not local_path:
            logger.error(f"Failed to download {file_name}")
            return

        # 2. Read Content
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # 3. Parse Frontmatter
        metadata = {}
        body_text = content
        
        if content.startswith("---"):
            try:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    body_text = parts[2]
                    metadata = yaml.safe_load(frontmatter)
            except Exception as e:
                logger.warning(f"Failed to parse frontmatter for {file_name}: {e}")
        
        # 4. Parse (AI Extraction) using Raw Text
        parsed_data = parse_cv_from_text(body_text, file_name, metadata=metadata)
        
        if not parsed_data:
            logger.error(f"Failed to parse {file_name}")
            return

        # 5. Save JSON Locally
        base_name = os.path.splitext(file_name)[0]
        json_filename = f"{base_name}_extracted.json"
        if not os.path.exists(JSON_OUTPUT_DIR):
            os.makedirs(JSON_OUTPUT_DIR)
        json_output_path = os.path.join(JSON_OUTPUT_DIR, json_filename)
        
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
        # 6. Upload JSON to Drive
        json_file_id, json_link = upload_file_to_folder(drive_service, json_output_path, output_folder_id)
        
        # Fallback if webViewLink is missing
        if not json_link and json_file_id:
            json_link = f"https://drive.google.com/file/d/{json_file_id}/view"

        logger.info(f"SUCCESS: Extracted {file_name} -> {json_filename} ({json_link})")
        
        # 7. Generate Report Row
        # Format Links
        md_url = f"https://drive.google.com/file/d/{file_id}/view"
        md_link_formula = create_hyperlink_formula(md_url, "Voir MD")
        
        if json_link:
             json_link_formula = create_hyperlink_formula(json_link, "Voir JSON")
        else:
             json_link_formula = ""
        
        try:
            # We assume the file will be moved to Processed if successful
            report_row = format_candidate_row(
                parsed_data, 
                md_link=md_link_formula, 
                emplacement="Processed",
                json_link=json_link_formula
            )
            report_buffer.append(report_row)
            logger.info(f"Added to Report Buffer: {file_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to generate report row for {file_name}: {e}")
            return False

    except Exception as e:
        logger.error(f"Error processing {file_name}: {e}", exc_info=True)
        return False

def main():
    load_dotenv()
    logger.info("--- Starting Pipeline 2: EXTRACTION (Excel-Driven) ---")

    # 1. Configuration
    json_output_folder_id = os.environ.get('JSON_OUTPUT_FOLDER_ID')
    # Load Config
    json_output_folder_id = os.getenv('JSON_OUTPUT_FOLDER_ID')
    email_sheet_id = os.getenv('EMAIL_SHEET_ID')
    source_sheet_name = os.getenv('EMAIL_SHEET_NAME', 'Contacts') # Default to 'Contacts' (Plural)
    dest_sheet_name = "Candidats"
    
    # Fallback for JSON Folder
    if not json_output_folder_id:
        json_output_folder_id = os.getenv('CV_TO_JSON_FOLDER_ID')

    if not json_output_folder_id or not email_sheet_id:
        logger.error("Missing configuration. Please set JSON_OUTPUT_FOLDER_ID and EMAIL_SHEET_ID in .env")
        return

    # Initialize Services
    try:
        drive_service = get_drive_service()
        sheets_service = get_sheets_service()
        
        # Log Service Account Email for Debugging
        try:
            about = drive_service.about().get(fields="user").execute()
            sa_email = about.get('user', {}).get('emailAddress')
            logger.info(f"Authenticated as: {sa_email}")
            logger.info("Ensure this email has 'Editor' access to your Google Drive folder and Sheet.")
        except Exception as e:
            logger.warning(f"Could not determine Service Account email: {e}")
            
    except Exception as e:
        logger.error(f"Failed to initialize Google Services: {e}")
        return

    # Ensure Headers in Dest Sheet
    try:
        ensure_report_headers(sheets_service, email_sheet_id, dest_sheet_name)
    except Exception as e:
        logger.error(f"Failed to ensure headers: {e}")

    # 2. Sync Step: Source (Contact) -> Dest (Candidats)
    logger.info(f"Reading Source Sheet '{source_sheet_name}'...")
    try:
        source_rows = get_sheet_values(sheets_service, email_sheet_id, source_sheet_name, value_render_option='FORMULA')
    except HttpError as e:
        if e.resp.status == 400:
            logger.warning(f"Failed to read sheet '{source_sheet_name}'. Checking available sheets...")
            # List available sheets
            try:
                sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=email_sheet_id).execute()
                sheets = sheet_metadata.get('sheets', '')
                sheet_names = [s.get("properties", {}).get("title") for s in sheets]
                logger.info(f"Available Sheets: {sheet_names}")
                
                # Auto-Fix: Check for "Contacts" if we were looking for "Contact"
                if source_sheet_name == "Contact" and "Contacts" in sheet_names:
                    logger.info("Found 'Contacts' sheet. Switching source sheet name to 'Contacts'.")
                    source_sheet_name = "Contacts"
                    source_rows = get_sheet_values(sheets_service, email_sheet_id, source_sheet_name, value_render_option='FORMULA')
                else:
                    logger.error("Could not auto-correct sheet name. Please update EMAIL_SHEET_NAME in .env.")
                    raise e
            except Exception as meta_err:
                logger.error(f"Failed to list/recover sheets: {meta_err}")
                raise e
        else:
            raise e
    
    logger.info(f"Reading Dest Sheet '{dest_sheet_name}'...")
    dest_rows = get_sheet_values(sheets_service, email_sheet_id, dest_sheet_name, value_render_option='FORMULA')

    # 2. Sync Step: Source (Contact) -> Dest (Candidats)
    # STRICT 1:1 SYNC by Row Index
    
    # We need to handle:
    # 1. New Rows (Append)
    # 2. Updated Rows (Update J, L, N if changed)
    # 3. Empty Values (Copy as is)
    
    updates = []
    rows_to_append = []
    
    # Iterate Source Rows (Skip Header i=0)
    if source_rows:
        for i, src_row in enumerate(source_rows):
            if i == 0: continue
            
            # Source Indices
            # Col A (0) = CV Link
            # Col F (5) = Languages (was Col E Emplacement)
            # Col G (6) = MD Link
            
            src_cv = src_row[0] if len(src_row) > 0 else ""
            src_lang = src_row[5] if len(src_row) > 5 else "" # Col F is Index 5
            src_md = src_row[6] if len(src_row) > 6 else ""
            
            # Check against Dest Row
            if dest_rows and i < len(dest_rows):
                dst_row = dest_rows[i]
                
                # Dest Indices (New Layout)
                # Col J (9) = Languages (Source)
                # Col L (11) = MD Link
                # Col N (13) = CV Link
                
                dst_lang = dst_row[9] if len(dst_row) > 9 else ""
                dst_md = dst_row[11] if len(dst_row) > 11 else ""
                dst_cv = dst_row[13] if len(dst_row) > 13 else ""
                
                # Compare
                if (src_cv != dst_cv) or (src_lang != dst_lang) or (src_md != dst_md):
                    # Update Needed!
                    # Update Languages (J)
                    updates.append({
                        'range': f"'{dest_sheet_name}'!J{i+1}",
                        'values': [[src_lang]]
                    })
                    # Update MD Link (L)
                    updates.append({
                        'range': f"'{dest_sheet_name}'!L{i+1}",
                        'values': [[src_md]]
                    })
                    # Update CV Link (N)
                    updates.append({
                        'range': f"'{dest_sheet_name}'!N{i+1}",
                        'values': [[src_cv]]
                    })
            else:
                # Dest row does not exist -> Append
                # Create skeleton row
                new_row = [""] * 14
                new_row[9] = src_lang
                new_row[11] = src_md
                new_row[13] = src_cv
                rows_to_append.append(new_row)

    # Execute Updates
    if updates:
        logger.info(f"Sync: Updating {len(updates)//3} existing rows in Dest...")
        
        chunk_size = 1000
        for k in range(0, len(updates), chunk_size):
            chunk = updates[k:k+chunk_size]
            body = {'data': chunk, 'valueInputOption': 'USER_ENTERED'}
            try:
                sheets_service.spreadsheets().values().batchUpdate(
                    spreadsheetId=email_sheet_id, body=body
                ).execute()
            except Exception as e:
                logger.error(f"Failed to sync updates chunk {k}: {e}")
        logger.info("Sync: Updates complete.")

    # Execute Appends
    if rows_to_append:
        logger.info(f"Sync: Appending {len(rows_to_append)} new rows to Dest...")
        append_batch_to_sheet(sheets_service, email_sheet_id, rows_to_append, dest_sheet_name)
    
    if not updates and not rows_to_append:
        logger.info("Sync: Dest sheet is fully synchronized with Source.")

    # 3. Process Step: Identify Rows needing JSON
    logger.info("Re-reading Dest sheet to identify pending tasks...")
    dest_rows = get_sheet_values(sheets_service, email_sheet_id, dest_sheet_name, value_render_option='FORMULA')
    
    tasks = [] # List of (file_id, cv_link, row_index)
    clear_buffer = [] # List of batch updates for "Supprimer"
    
    if dest_rows:
        for i, row in enumerate(dest_rows):
            if i == 0: continue
            
            # Check Action (Col K, Index 10)
            action = row[10] if len(row) > 10 else ""
            action = str(action).strip().lower()

            if action == "supprimer":
                # Clear AI columns (A-I, M) and Action (K)
                # Preserve J (Languages), L (MD Link), N (CV Link)
                # We can batch these clears.
                # Clear A-I
                clear_buffer.append({
                    'range': f"'{dest_sheet_name}'!A{i+1}:I{i+1}",
                    'values': [[""] * 9]
                })
                # Clear K (Action)
                clear_buffer.append({
                    'range': f"'{dest_sheet_name}'!K{i+1}",
                    'values': [[""]]
                })
                # Clear M (JSON Link)
                clear_buffer.append({
                    'range': f"'{dest_sheet_name}'!M{i+1}",
                    'values': [[""]]
                })
                continue # Skip processing

            # Check JSON Link (Col M, Index 12)
            json_link = row[12] if len(row) > 12 else ""
            
            if not json_link or action == "retraiter":
                # Needs Processing!
                # Get File ID from MD Link (Col L, Index 11)
                if len(row) > 11:
                    md_link = row[11]
                    match = re.search(r'/d/([a-zA-Z0-9_-]+)', md_link)
                    if match:
                        file_id = match.group(1)
                        cv_link = row[13] if len(row) > 13 else ""
                        languages_source = row[9] if len(row) > 9 else "" # Preserve synced value
                        tasks.append({
                            'file_id': file_id,
                            'cv_link': cv_link,
                            'languages_source': languages_source,
                            'row_index': i # 0-based index in 'values' list. Excel row is i+1.
                        })

    # Execute Clears
    if clear_buffer:
        logger.info(f"Clearing {len(clear_buffer)//3} rows marked as 'Supprimer'...")
        body = {'data': clear_buffer, 'valueInputOption': 'USER_ENTERED'}
        try:
            sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=email_sheet_id, body=body
            ).execute()
        except Exception as e:
            logger.error(f"Failed to clear rows: {e}")

    logger.info(f"Found {len(tasks)} rows needing processing (Missing JSON or 'Retraiter').")
    
    # Batch Limit
    batch_limit = 1 # Decreased to 1 as requested
    tasks_to_process = tasks[:batch_limit]
    logger.info(f"Processing {len(tasks_to_process)} tasks (Batch Limit: {batch_limit})...")

    report_buffer = []
    
    # Parallel Execution
    max_workers = 5
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(process_file_by_id, t['file_id'], t['cv_link'], json_output_folder_id, i+1, len(tasks_to_process), t['languages_source']): t
            for i, t in enumerate(tasks_to_process)
        }
        
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                success, report_row, _ = future.result()
                if success and report_row:
                    # We have a full report row.
                    # We want to UPDATE the specific row index `task['row_index']`.
                    # But `report_row` is a list of values.
                    # We can construct a batch update for this specific row.
                    report_buffer.append({
                        'range': f"'{dest_sheet_name}'!A{task['row_index'] + 1}", # A(i+1)
                        'values': [report_row]
                    })
            except Exception as e:
                logger.error(f"Task failed for {task['file_id']}: {e}")

    # Flush Updates
    if report_buffer:
        logger.info(f"Flushing {len(report_buffer)} updates to '{dest_sheet_name}'...")
        # We can't use upsert_batch_to_sheet because we are updating specific rows by index.
        # We need a simple batch update.
        body = {'data': report_buffer, 'valueInputOption': 'USER_ENTERED'}
        try:
            sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=email_sheet_id, body=body
            ).execute()
            logger.info("Updates successful.")
        except Exception as e:
            logger.error(f"Failed to flush updates: {e}")

    logger.info("--- Extraction Pipeline Finished ---")

if __name__ == "__main__":
    main()
