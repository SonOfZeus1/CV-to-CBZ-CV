import os
import json
import logging
import io
from dotenv import load_dotenv
from googleapiclient.http import MediaIoBaseDownload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from google_drive import get_drive_service, list_files_in_folder, download_file, upload_file_to_folder
from parsers import parse_cv

# Configuration
DOWNLOADS_DIR = "downloads"
JSON_OUTPUT_DIR = "output_jsons"

def process_file(file_item, drive_service, output_folder_id):
    """
    Process a single file: Download -> Parse -> Upload JSON
    """
    file_id = file_item['id']
    file_name = file_item['name']
    
    logger.info(f"Processing file: {file_name} ({file_id})")
    
    try:
        # 1. Download PDF
        local_path = download_file(drive_service, file_id, file_name, DOWNLOADS_DIR)
        if not local_path:
            logger.error(f"Failed to download {file_name}")
            return

        # 2. Parse (AI Extraction)
        parsed_data = parse_cv(local_path)
        
        if not parsed_data:
            logger.error(f"Failed to parse {file_name}")
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
        # We upload to the output folder. 
        # Note: We don't check for duplicates here as per "simple" requirement, 
        # but upload_file_to_folder usually creates a new file.
        json_file_id, json_link = upload_file_to_folder(drive_service, json_output_path, output_folder_id)
        
        logger.info(f"SUCCESS: Extracted {file_name} -> {json_filename} ({json_link})")

    except Exception as e:
        logger.error(f"Error processing {file_name}: {e}", exc_info=True)

def main():
    load_dotenv()
    logger.info("--- Starting Pipeline 1: EXTRACTION (Folder -> Folder) ---")

    source_folder_id = os.environ.get('CV_TO_JSON_FOLDER_ID')
    json_output_folder_id = os.environ.get('JSON_OUTPUT_FOLDER_ID')
    
    if not source_folder_id:
        logger.error("Missing CV_TO_JSON_FOLDER_ID in .env")
        return

    if not json_output_folder_id:
        logger.warning("JSON_OUTPUT_FOLDER_ID not set. Using CV_TO_JSON_FOLDER_ID as fallback.")
        json_output_folder_id = source_folder_id

    try:
        drive_service = get_drive_service()
    except Exception as e:
        logger.critical(f"Auth Error: {e}")
        return

    # 1. List files in Source Folder
    logger.info(f"Listing files in Source Folder ({source_folder_id})...")
    files = list_files_in_folder(drive_service, source_folder_id)
    
    if not files:
        logger.info("No files found in Source Folder.")
        return
        
    logger.info(f"Found {len(files)} files to process.")

    # 2. Process each file
    for file_item in files:
        process_file(file_item, drive_service, json_output_folder_id)

    logger.info("--- Extraction Pipeline Finished ---")

if __name__ == "__main__":
    main()
