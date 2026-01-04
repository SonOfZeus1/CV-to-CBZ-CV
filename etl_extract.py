import os
import json
import logging
import io
import yaml
import re
from dotenv import load_dotenv
from googleapiclient.http import MediaIoBaseDownload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google_drive import (
    get_drive_service, list_files_in_folder, download_file, 
    upload_file_to_folder, get_or_create_folder,
    get_sheets_service, append_batch_to_sheet, upsert_batch_to_sheet, ensure_report_headers,
    remove_empty_rows
)
from parsers import parse_cv_from_text
from report_generator import format_candidate_row

# Configuration
DOWNLOADS_DIR = "downloads"
JSON_OUTPUT_DIR = "output_jsons"

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
        
        logger.info(f"SUCCESS: Extracted {file_name} -> {json_filename} ({json_link})")
        
        # 7. Generate Report Row
        # We need the MD link. If we don't have it explicitly, we can construct it or leave it empty.
        # The MD file is in the Index Folder. We can try to construct a link if we know the ID.
        # file_item['webViewLink'] might be available if we requested fields.
        # For now, let's use a placeholder or try to get it.
        md_link = f"https://drive.google.com/file/d/{file_id}/view"
        
        try:
            # We assume the file will be moved to Processed if successful
            report_row = format_candidate_row(parsed_data, md_link, emplacement="Processed")
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
    logger.info("--- Starting Pipeline 2: EXTRACTION (Markdown -> JSON + Excel Report) ---")

    # 1. Use CV_TO_JSON_FOLDER_ID directly as the Index Folder
    index_folder_id = os.environ.get('CV_TO_JSON_FOLDER_ID')
    if not index_folder_id:
        logger.error("Missing CV_TO_JSON_FOLDER_ID in .env")
        return
        
    logger.info(f"Using Index Folder ID from env: {index_folder_id}")

    json_output_folder_id = os.environ.get('JSON_OUTPUT_FOLDER_ID')
    if not json_output_folder_id:
        logger.warning("JSON_OUTPUT_FOLDER_ID not set. Using CV_TO_JSON_FOLDER_ID as fallback.")
        json_output_folder_id = index_folder_id

    # Excel Configuration
    email_sheet_id = os.environ.get('EMAIL_SHEET_ID')
    if not email_sheet_id:
        logger.warning("EMAIL_SHEET_ID not set. Excel reporting will be skipped.")
    
    try:
        drive_service = get_drive_service()
        sheets_service = get_sheets_service() if email_sheet_id else None
    except Exception as e:
        logger.critical(f"Auth Error: {e}")
        return

    # Ensure Headers
    if sheets_service and email_sheet_id:
        try:
            ensure_report_headers(sheets_service, email_sheet_id, "Candidats")
        except Exception as e:
            logger.error(f"Failed to ensure headers: {e}")

    # 1.5. Initialize Processed Folder
    processed_folder_id = get_or_create_folder(drive_service, "_Processed_JSON", parent_id=index_folder_id)
    logger.info(f"Processed Folder ID: {processed_folder_id}")

    # 2. List files in Index Folder (Source)
    logger.info(f"Listing files in Index Folder...")
    try:
        source_files = list_files_in_folder(drive_service, index_folder_id, mime_types=['text/markdown'])
    except Exception as e:
        logger.critical(f"Error listing files in folder {index_folder_id}: {e}")
        return
    
    source_file_map = {f['id']: f for f in source_files}
    logger.info(f"Found {len(source_files)} files in Source Folder.")

    # 2.5. Controller Logic (Sync with Excel)
    existing_files_in_excel = set()
    files_to_reprocess = set()
    
    if sheets_service and email_sheet_id:
        try:
            logger.info("Reading 'Candidats' sheet for Controller Logic...")
            rows = get_sheet_values(sheets_service, email_sheet_id, "Candidats")
            
            if rows:
                # Column Mapping (0-based):
                # J (Index 9) = MD Link (contains File ID)
                # K (Index 10) = Action
                
                rows_to_delete = [] 
                
                for i, row in enumerate(rows):
                    if i == 0: continue # Skip header
                    
                    # Extract File ID from MD Link
                    file_id = None
                    if len(row) > 9:
                        md_link = row[9]
                        match = re.search(r'/d/([a-zA-Z0-9_-]+)', md_link)
                        if match:
                            file_id = match.group(1)
                            existing_files_in_excel.add(file_id)
                    
                    # Check Action
                    action = ""
                    if len(row) > 10:
                        action = row[10].strip().lower()
                    
                    if action == "supprimer":
                        rows_to_delete.append(i)
                        logger.info(f"Row {i+1} marked for DELETION.")
                        
                    elif action == "retraiter":
                        rows_to_delete.append(i)
                        if file_id:
                            # Move file BACK to Source (if it's in Processed)
                            # We don't know where it is exactly, but we try to move it to Source
                            try:
                                move_file(drive_service, file_id, processed_folder_id, index_folder_id)
                                logger.info(f"Moved file {file_id} back to Source for reprocessing.")
                                files_to_reprocess.add(file_id)
                                # Remove from existing set so it gets picked up
                                if file_id in existing_files_in_excel:
                                    existing_files_in_excel.remove(file_id)
                            except Exception as move_err:
                                logger.warning(f"Failed to move file {file_id} back to source: {move_err}")

                # Execute Deletions
                if rows_to_delete:
                    rows_to_delete.sort(reverse=True)
                    
                    # Get Sheet ID
                    sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=email_sheet_id).execute()
                    sheets = sheet_metadata.get('sheets', '')
                    sheet_int_id = 0
                    for s in sheets:
                        if s.get("properties", {}).get("title") == "Candidats":
                            sheet_int_id = s.get("properties", {}).get("sheetId")
                            break
                    
                    requests = []
                    for row_idx in rows_to_delete:
                        requests.append({
                            "deleteDimension": {
                                "range": {
                                    "sheetId": sheet_int_id,
                                    "dimension": "ROWS",
                                    "startIndex": row_idx,
                                    "endIndex": row_idx + 1
                                }
                            }
                        })
                    
                    if requests:
                        body = {'requests': requests}
                        sheets_service.spreadsheets().batchUpdate(spreadsheetId=email_sheet_id, body=body).execute()
                        logger.info(f"Executed {len(requests)} row deletions.")

        except Exception as e:
            logger.error(f"Error in Controller Logic: {e}")

    # 2.6. Pre-flight Sync: Move files already in Excel to Processed
    logger.info("Running Pre-flight Sync...")
    files_to_process = []
    
    for f in source_files:
        f_id = f['id']
        
        # If file is in Excel AND NOT marked for reprocessing -> Move to Processed
        if f_id in existing_files_in_excel and f_id not in files_to_reprocess:
            try:
                move_file(drive_service, f_id, index_folder_id, processed_folder_id)
                # logger.info(f"Moved already processed file {f['name']} to _Processed_JSON")
            except Exception as e:
                logger.warning(f"Failed to move {f['name']} to processed: {e}")
        else:
            # File is NOT in Excel (or is reprocessed) -> Add to queue
            if f['name'].endswith('.md'):
                files_to_process.append(f)

    # 3. Process files (Batch Limit: 10)
    # Sort: Reprocess files first? They are already in the list.
    # Just apply limit.
    files_to_process = files_to_process[:10]
    logger.info(f"Processing {len(files_to_process)} files (Batch Limit: 10)...")

    report_buffer = []

    for file_item in files_to_process:
        # Process
        success = process_file(file_item, drive_service, json_output_folder_id, report_buffer)
        
        # If success, move to Processed
        if success: # process_file needs to return True/False
             try:
                move_file(drive_service, file_item['id'], index_folder_id, processed_folder_id)
                logger.info(f"Moved {file_item['name']} to _Processed_JSON")
             except Exception as e:
                logger.error(f"Failed to move {file_item['name']} after processing: {e}")

    # Flush Report Buffer
    if report_buffer and sheets_service and email_sheet_id:
        logger.info(f"Flushing {len(report_buffer)} rows to 'Candidats'...")
        try:
            # Use Upsert (Update if exists, Append if new)
            # Email is at index 2 (Name, Surname, Email...)
            upsert_batch_to_sheet(sheets_service, email_sheet_id, report_buffer, sheet_name="Candidats", email_col_index=2)
            logger.info("Report flush successful.")
            
            # Clean up empty rows
            remove_empty_rows(sheets_service, email_sheet_id, "Candidats")
            
        except Exception as e:
            logger.error(f"Failed to flush report: {e}")

    logger.info("--- Extraction Pipeline Finished ---")

if __name__ == "__main__":
    main()
