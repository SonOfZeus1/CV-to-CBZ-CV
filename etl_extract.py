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

from google_drive import get_drive_service, list_files_in_folder, download_file, upload_file_to_folder, get_or_create_folder
from parsers import parse_cv_from_text

# Configuration
DOWNLOADS_DIR = "downloads"
JSON_OUTPUT_DIR = "output_jsons"

def process_file(file_item, drive_service, output_folder_id):
    """
    Process a single MD file: Read Content -> Parse -> Upload JSON
    """
    file_id = file_item['id']
    file_name = file_item['name']
    
    logger.info(f"Processing file: {file_name} ({file_id})")
    
    try:
        # 1. Download MD File (or read content directly if small)
        # Since MD files are small, we can download to memory or temp file
        local_path = download_file(drive_service, file_id, file_name, DOWNLOADS_DIR)
        if not local_path:
            logger.error(f"Failed to download {file_name}")
            return

        # 2. Read Content
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # 3. Parse Frontmatter
        # Frontmatter is between first two ---
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
        # We pass metadata (email, phone) to help AI
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

    except Exception as e:
        logger.error(f"Error processing {file_name}: {e}", exc_info=True)

def main():
    load_dotenv()
    logger.info("--- Starting Pipeline 2: EXTRACTION (Markdown -> JSON) ---")

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

    # 1. Resolve Index Folder
    # Strategy: Try to see if the provided ID is the index folder itself.
    # If that fails or it's not, assume it's a parent and search inside.
    index_folder_id = None
    
    try:
        source_folder_meta = drive_service.files().get(fileId=source_folder_id, fields="name").execute()
        if source_folder_meta.get('name') == '_cv_index_v2':
            logger.info(f"Provided folder ID IS the index folder: {source_folder_id}")
            index_folder_id = source_folder_id
    except Exception as e:
        logger.warning(f"Could not resolve provided ID as a folder directly (Error: {e}). Assuming it is a Parent Folder and searching inside...")

    if not index_folder_id:
        # Search for _cv_index_v2 inside source folder
        logger.info(f"Looking for _cv_index_v2 inside {source_folder_id}...")
        query = f"'{source_folder_id}' in parents and name = '_cv_index_v2' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        try:
            results = drive_service.files().list(q=query, fields="files(id, name)").execute()
            items = results.get('files', [])
            
            if items:
                index_folder_id = items[0]['id']
                logger.info(f"Found Index Folder: {index_folder_id}")
            else:
                logger.error(f"_cv_index_v2 folder not found inside {source_folder_id}!")
                return
        except Exception as e:
             logger.critical(f"Error searching for index folder: {e}")
             return

    # 2. List files in Index Folder
    logger.info(f"Listing files in Index Folder...")
    files = list_files_in_folder(drive_service, index_folder_id)
    
    if not files:
        logger.info("No files found in Index Folder.")
        return
        
    logger.info(f"Found {len(files)} files to process.")

    # 3. Process each file (Limit to 200)
    files_to_process = [f for f in files if f['name'].endswith('.md')][:200]
    logger.info(f"Processing {len(files_to_process)} files (Batch Limit: 200)...")

    for file_item in files_to_process:
        process_file(file_item, drive_service, json_output_folder_id)

    logger.info("--- Extraction Pipeline Finished ---")

if __name__ == "__main__":
    main()
