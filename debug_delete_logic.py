import os
import logging
from google_drive import get_sheets_service, get_sheet_values

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock ID - Assuming user runs this with correct env
# We need to find the Sheet ID. It's usually hardcoded or passed.
# In extract_emails.py, it takes it from args or env.
# Let's rely on finding it or ask user.
# For now, I'll use a placeholder or try to read from main.py if possible.
# Actually, I'll ask the user to provide it or just use the one from main.py if I can find it.
# Let's check main.py or args.

SHEET_ID = "15kC8g4CjV8KqgO-iJjH_yXFpdwkFk4d5bJgJqOqW_sE" # Found in previous logs or context if possible. 
# Wait, I don't have the sheet ID in context.
# I will make the script accept it or try to fetch it.
# Let's check RECENT FILE `reproduce_issue.py` (it might have it).

def debug_delete_logic(sheet_id):
    service = get_sheets_service()
    sheet_name = "Feuille 1"
    
    logger.info(f"Reading sheet {sheet_id}...")
    rows = get_sheet_values(service, sheet_id, sheet_name, value_render_option='FORMULA')
    
    if not rows:
        logger.info("Sheet is empty.")
        return

    expected_header = ["Filename", "Email", "Phone", "Status", "Emplacement", "Language", "Lien Index"]
    header = rows[0]
    data = rows[1:]
    offset = 1
    
    logger.info(f"Header found: {header}")
    logger.info(f"Expected: {expected_header}")
    
    if header != expected_header:
        logger.info("Header Mismatch detected.")
        if "Email" not in header:
            logger.info("'Email' NOT in header. Treating all rows as data.")
            data = rows
            offset = 0
            logger.info(f"Offset set to {offset}")
        else:
            logger.info("'Email' IS in header. Preserving offset 1.")
            
    rows_to_delete = []
    
    for i, row in enumerate(data):
        row_index = i + offset
        status = str(row[3]).strip() if len(row) > 3 else ""
        
        if status.lower() == "delete":
            logger.info(f"MATCH: Row {i} (Data Index) has 'delete'.")
            logger.info(f"Calculated Sheet Index: {row_index} (Expected to be Row {row_index + 1})")
            logger.info(f"Row Content: {row}")
            rows_to_delete.append(row_index)
            
            # Verify what is at this index in ORIGINAL rows
            if row_index < len(rows):
                logger.info(f"VERIFY: Content at rows[{row_index}]: {rows[row_index]}")
            else:
                logger.info(f"VERIFY: Index {row_index} is out of bounds of read rows!")

if __name__ == "__main__":
    # Allow passing ID
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else None
    if not sid:
        print("Please provide Sheet ID as argument.")
    else:
        debug_delete_logic(sid)
