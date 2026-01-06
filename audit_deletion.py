import logging
import sys
from google_drive import get_sheets_service, get_sheet_values

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def audit_deletion_logic(sheet_id, sheet_name="Feuille 1"):
    service = get_sheets_service()
    
    logger.info(f"--- AUDIT SCRIPT STARTED ---")
    logger.info(f"Target Sheet ID: {sheet_id}")
    logger.info(f"Target Sheet Name: {sheet_name}")

    # 1. Read Rows
    logger.info("Reading sheet values (A:Z)...")
    rows = get_sheet_values(service, sheet_id, sheet_name, value_render_option='UnformattedValue')
    
    if not rows:
        logger.error("Sheet is empty! Cannot audit.")
        return

    logger.info(f"Total Rows Read: {len(rows)}")
    
    # 2. Replicate Header Logic
    expected_header = ["Filename", "Email", "Phone", "Status", "Emplacement", "Language", "Lien Index"]
    first_row = rows[0]
    
    logger.info(f"Row 0 Content: {first_row}")
    
    is_header = True
    if len(first_row) > 1 and '@' in str(first_row[1]) and "Email" not in str(first_row[1]):
        is_header = False
        logger.info("-> Logic: Row 0 looks like DATA (Found '@').")
    elif first_row == expected_header:
        is_header = True
        logger.info("-> Logic: Row 0 matches Expected Header exactly.")
    elif "Email" in str(first_row):
        is_header = True
        logger.info("-> Logic: Row 0 contains 'Email'. Treating as Header.")
    else:
        is_header = not (len(first_row) > 1 and '@' in str(first_row[1]))
        logger.info(f"-> Logic: Fallback check. Is Header? {is_header}")

    offset = 1 if is_header else 0
    data = rows[1:] if is_header else rows
    
    logger.info(f"Calculated Offset: {offset}")
    if is_header:
        logger.info(f"Data slice starts at Row {offset+1} (Index {offset})")
    else:
        logger.info("Data includes Row 1 (Index 0)")

    # 3. Find Rows to Delete
    logger.info("\n--- SCANNING FOR 'DELETE' STATUS ---")
    found_delete = False
    
    for i, row in enumerate(data):
        # Calculate Index
        calculated_index = i + offset
        
        # Get Status
        status = str(row[3]).strip() if len(row) > 3 else ""
        
        if status.lower() == "delete":
            found_delete = True
            logger.info(f"\n[MATCH FOUND] at Scan Index {i}")
            logger.info(f"Row Content: {row}")
            logger.info(f"Calculated Sheet Index to Delete: {calculated_index} (Visual Row {calculated_index + 1})")
            
            # Verify Content at Calculated Index from ORIGINAL rows
            if calculated_index < len(rows):
                target_row = rows[calculated_index]
                logger.info(f"TARGET VERIFICATION (rows[{calculated_index}]): {target_row}")
                
                if target_row == row:
                    logger.info("SUCCESS: Calculated target matches the row with 'delete'. Logic is CORRECT.")
                else:
                    logger.info("FAILURE: Calculated target DOES NOT MATCH the row with 'delete'.")
                    logger.info("!!! ALGORITHM WILL DELETE WRONG ROW !!!")
                    
                    # Try to find where the row actually is
                    if offset == 1:
                        logger.info(f"Checking Index-1 (rows[{calculated_index - 1}]): {rows[calculated_index - 1]}")
            else:
                logger.error(f"Calculated Index {calculated_index} is OUT OF BOUNDS!")

    if not found_delete:
        logger.info("No rows with status 'Delete' found.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python audit_deletion.py <SHEET_ID> [SHEET_NAME]")
        sys.exit(1)
        
    sid = sys.argv[1]
    sname = sys.argv[2] if len(sys.argv) > 2 else "Feuille 1"
    audit_deletion_logic(sid, sname)
