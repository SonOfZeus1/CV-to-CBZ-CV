import os
import json
import logging
import io
from dotenv import load_dotenv
from googleapiclient.http import MediaIoBaseDownload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google_drive import get_drive_service, get_sheets_service, fetch_actionable_cvs, update_sheet_row, upload_file_to_folder

# ... (imports and logging config remain same)

def process_extract_row(row_data, drive_service, sheets_service, sheet_id, output_folder_id, sheet_name):
    """
    Pipeline 2: Extraction (Flag & Process)
    PDF -> JSON
    """
    row_num = row_data['row'] # 1-based sheet row number
    row_index = row_num - 1   # 0-based index for update_sheet_row
    file_id = row_data['file_id']
    file_name = row_data['file_name']
    
    logger.info(f"EXTRACT Row {row_num}: {file_name}")
    
    # Update status to PROCESSING (Column D, index 3)
    # We only update the Status column
    # update_sheet_row expects a list of values. To update only column D, we can't easily use it 
    # if it overwrites the whole row range.
    # But wait, update_sheet_row calculates range based on values length.
    # If we want to update ONLY column D (Status), we need to target that specific cell.
    # My update_sheet_row implementation calculates range starting from A.
    # I should probably just update the specific cell manually here or modify update_sheet_row.
    # Actually, let's just use the sheets service directly here for precision or update update_sheet_row to accept start_col.
    
    # Let's use a helper here to update specific cells
    def update_cell(r_idx, c_idx, val):
        # c_idx: 0=A, 1=B, 2=C, 3=D, 4=E
        col_char = chr(ord('A') + c_idx)
        range_name = f"{sheet_name}!{col_char}{row_num}"
        body = {'values': [[val]]}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body=body
        ).execute()

    update_cell(row_index, 3, "EXTRACTION_EN_COURS")
    
    # 1. Download PDF
    local_path = download_file_by_id(drive_service, file_id, file_name, DOWNLOADS_DIR)
    if not local_path:
        update_cell(row_index, 3, "ERREUR_DOWNLOAD")
        return

    try:
        # 2. Parse (AI Extraction)
        parsed_data = parse_cv(local_path)
        
        if not parsed_data:
            update_cell(row_index, 3, "ERREUR_PARSING")
            return

        # 3. Save JSON Locally
        base_name = os.path.splitext(file_name)[0]
        json_filename = f"{base_name}_extracted.json"
        if not os.path.exists(JSON_OUTPUT_DIR):
            os.makedirs(JSON_OUTPUT_DIR)
        json_output_path = os.path.join(JSON_OUTPUT_DIR, json_filename)
        
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
        # 4. Upload JSON to Drive
        json_file_id, json_link = upload_file_to_folder(drive_service, json_output_path, output_folder_id)
        
        # 5. Update Sheet -> FAIT + JSON Link
        # Update Status (Col D) and JSON Link (Col E)
        range_name = f"{sheet_name}!D{row_num}:E{row_num}"
        body = {'values': [["FAIT", json_link]]}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body=body
        ).execute()
        
        logger.info(f"SUCCESS EXTRACT Row {row_num}: {file_name}")

    except Exception as e:
        logger.error(f"Error extracting {file_name}: {e}", exc_info=True)
        update_cell(row_index, 3, f"ERREUR: {str(e)}")

def main():
    load_dotenv()
    logger.info("--- Starting Pipeline 2: EXTRACTION (Flag & Process) ---")

    sheet_id = os.environ.get('EMAIL_SHEET_ID') # Use the new Email Sheet ID
    # Fallback to SHEET_ID if EMAIL_SHEET_ID is not set (for backward compatibility or if user reuses same sheet)
    if not sheet_id:
        sheet_id = os.environ.get('SHEET_ID')

    source_folder_id = os.environ.get('SOURCE_FOLDER_ID') # Still need this? Actually we get file IDs from sheet.
    # But we need output folder for JSONs
    json_output_folder_id = os.environ.get('JSON_OUTPUT_FOLDER_ID')
    sheet_name = os.environ.get('EMAIL_SHEET_NAME', 'Feuille 1') # Use Email Sheet Name
    
    if not sheet_id:
        logger.error("Missing SHEET_ID (or EMAIL_SHEET_ID) in .env")
        return

    if not json_output_folder_id:
        logger.warning("JSON_OUTPUT_FOLDER_ID not set. Using SOURCE_FOLDER_ID as fallback.")
        json_output_folder_id = source_folder_id

    try:
        drive_service = get_drive_service()
        sheets_service = get_sheets_service()
    except Exception as e:
        logger.critical(f"Auth Error: {e}")
        return

    # Fetch rows with status "A TRAITER"
    logger.info(f"Fetching actionable CVs (A TRAITER) from {sheet_name}...")
    actionable_cvs = fetch_actionable_cvs(sheets_service, sheet_id, sheet_name=sheet_name, target_status="A TRAITER")
    
    if not actionable_cvs:
        logger.info("No CVs marked 'A TRAITER'.")
        return
        
    logger.info(f"Found {len(actionable_cvs)} CVs to process.")

    for row in actionable_cvs:
        process_extract_row(row, drive_service, sheets_service, sheet_id, json_output_folder_id, sheet_name)

    logger.info("--- Extraction Pipeline Finished ---")

if __name__ == "__main__":
    main()
