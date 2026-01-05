import os
import argparse
import logging
from google_drive import get_drive_service, get_sheets_service, list_files_in_folder, get_sheet_values, batch_update_rows
from dotenv import load_dotenv
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

def normalize_name(name):
    """Normalize name for matching (lowercase, remove accents/spaces)."""
    if not name: return ""
    return re.sub(r'[^a-z0-9]', '', name.lower())

def repair_links(folder_id, sheet_id, sheet_name="Contacts"):
    """
    Scans the given folder for MD files and updates the 'Lien MD' column in the sheet
    if the filename matches the candidate's name.
    """
    logger.info(f"Starting Link Repair...")
    logger.info(f"Scanning Folder: {folder_id}")
    
    try:
        service = get_drive_service()
        sheets_service = get_sheets_service()
        
        # 1. List Files in Folder
        files = list_files_in_folder(service, folder_id, mime_types=['text/markdown'])
        logger.info(f"Found {len(files)} MD files in folder.")
        
        # Build Map: Normalized Name -> File ID
        file_map = {}
        for f in files:
            # Filename format: First_Last_... or Last_First_...
            # We'll normalize the whole filename
            norm_name = normalize_name(f['name'])
            file_map[norm_name] = f['id']
            
        # 2. Read Sheet
        logger.info(f"Reading Sheet: {sheet_name}")
        rows = get_sheet_values(sheets_service, sheet_id, sheet_name)
        
        updates = []
        
        if rows:
            for i, row in enumerate(rows):
                if i == 0: continue # Skip header
                
                # Extract Name (Col B, C)
                first_name = row[1] if len(row) > 1 else ""
                last_name = row[2] if len(row) > 2 else ""
                
                if not first_name and not last_name:
                    continue
                
                # Construct search keys
                # Try: FirstLast, LastFirst
                key1 = normalize_name(f"{first_name}{last_name}")
                key2 = normalize_name(f"{last_name}{first_name}")
                
                found_id = None
                
                # Search in file map
                # We look for filenames that *contain* the name key
                # This is O(N*M), might be slow if many files.
                # Optimization: Check exact match first?
                # Filenames usually have extra junk.
                
                for fname, fid in file_map.items():
                    if key1 in fname or key2 in fname:
                        found_id = fid
                        break
                
                if found_id:
                    # Generate Link
                    new_link = f"https://drive.google.com/file/d/{found_id}/view"
                    
                    # Check if update needed (Col G, Index 6)
                    current_link = row[6] if len(row) > 6 else ""
                    
                    if found_id not in current_link:
                        logger.info(f"Row {i+1}: Found match for {first_name} {last_name} -> {found_id}")
                        updates.append({
                            'range': f"'{sheet_name}'!G{i+1}",
                            'values': [[new_link]]
                        })
        
        # 3. Execute Updates
        if updates:
            logger.info(f"Updating {len(updates)} links...")
            chunk_size = 1000
            for k in range(0, len(updates), chunk_size):
                chunk = updates[k:k+chunk_size]
                body = {'data': chunk, 'valueInputOption': 'USER_ENTERED'}
                sheets_service.spreadsheets().values().batchUpdate(
                    spreadsheetId=sheet_id, body=body
                ).execute()
            logger.info("Updates complete.")
        else:
            logger.info("No updates needed.")
            
    except Exception as e:
        logger.error(f"Repair failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair broken MD links in Excel.")
    parser.add_argument("--folder_id", required=True, help="ID of the folder containing MD files")
    parser.add_argument("--sheet_id", required=True, help="Google Sheet ID")
    parser.add_argument("--sheet_name", default="Contacts", help="Sheet Name (default: Contacts)")
    
    args = parser.parse_args()
    
    repair_links(args.folder_id, args.sheet_id, args.sheet_name)
