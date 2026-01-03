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
            report_row = format_candidate_row(parsed_data, md_link)
            report_buffer.append(report_row)
            logger.info(f"Added to Report Buffer: {file_name}")
        except Exception as e:
            logger.error(f"Failed to generate report row for {file_name}: {e}")

    except Exception as e:
        logger.error(f"Error processing {file_name}: {e}", exc_info=True)

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

    # 2. List files in Index Folder
    logger.info(f"Listing files in Index Folder...")
    try:
        files = list_files_in_folder(drive_service, index_folder_id, mime_types=['text/markdown'])
    except Exception as e:
        logger.critical(f"Error listing files in folder {index_folder_id}: {e}")
        return
    
    if not files:
        logger.info("No files found in Index Folder.")
        return
        
    logger.info(f"Found {len(files)} files to process.")

    # 2.5. Check "Action" Column (Retraiter / Supprimer)
    files_to_reprocess = set()
    if sheets_service and email_sheet_id:
        try:
            logger.info("Checking 'Action' column in 'Candidats'...")
            rows = get_sheet_values(sheets_service, email_sheet_id, "Candidats")
            
            if rows:
                # Identify rows to delete or reprocess
                # Column K (Action) is index 10. Column J (MD Link) is index 9.
                rows_to_delete = [] # Indices to delete
                
                for i, row in enumerate(rows):
                    if i == 0: continue # Skip header
                    
                    action = ""
                    if len(row) > 10:
                        action = row[10].strip().lower()
                    
                    if action == "supprimer":
                        rows_to_delete.append(i)
                        logger.info(f"Row {i+1} marked for DELETION.")
                        
                    elif action == "retraiter":
                        rows_to_delete.append(i) # Delete from sheet to re-add later
                        # Extract File ID from MD Link (Index 9)
                        if len(row) > 9:
                            md_link = row[9]
                            # Link format: https://drive.google.com/file/d/{file_id}/view
                            match = re.search(r'/d/([a-zA-Z0-9_-]+)', md_link)
                            if match:
                                file_id = match.group(1)
                                files_to_reprocess.add(file_id)
                                logger.info(f"Row {i+1} marked for REPROCESSING (File ID: {file_id}).")
                
                # Execute Deletions (if any)
                if rows_to_delete:
                    # Group into ranges (reverse order handled by remove_empty_rows logic, but here we do it manually or use batch_update)
                    # Actually, let's use a custom delete logic or just delete one by one? No, batch is better.
                    # We can reuse the logic from remove_empty_rows but passing specific indices.
                    # Or simpler: Just delete them.
                    # IMPORTANT: Delete from bottom to top!
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
                        logger.info(f"Executed {len(requests)} row deletions (Supprimer/Retraiter).")

        except Exception as e:
            logger.error(f"Error processing Action column: {e}")

    # 3. Process files (Batch Limit: 50)
    # Prioritize files_to_reprocess
    files_to_process = []
    
    # First, add reprocess files
    for f in files:
        if f['id'] in files_to_reprocess:
            files_to_process.append(f)
            
    # Then add others up to limit
    remaining_slots = 50 - len(files_to_process)
    if remaining_slots > 0:
        others = [f for f in files if f['name'].endswith('.md') and f['id'] not in files_to_reprocess][:remaining_slots]
        files_to_process.extend(others)
        
    logger.info(f"Processing {len(files_to_process)} files (Batch Limit: 50)...")

    report_buffer = []

    for file_item in files_to_process:
        process_file(file_item, drive_service, json_output_folder_id, report_buffer)

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
