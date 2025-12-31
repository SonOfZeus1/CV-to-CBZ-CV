import os
import logging
from dotenv import load_dotenv
from google_drive import get_drive_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def debug_permissions():
    load_dotenv()
    logger.info("--- DEBUGGING PERMISSIONS ---")
    
    try:
        service = get_drive_service()
        
        # 1. Get About info (User email)
        about = service.about().get(fields="user").execute()
        user_email = about['user']['emailAddress']
        logger.info(f"Connected as Service Account: {user_email}")
        logger.info("IMPORTANT: You must share your folders with THIS email address.\n")

        # 2. List ALL folders visible to this account
        logger.info("Listing ALL folders visible to this account (limit 20)...")
        results = service.files().list(
            q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            pageSize=20,
            fields="nextPageToken, files(id, name, parents)"
        ).execute()
        items = results.get('files', [])

        if not items:
            logger.warning("No folders found! The robot cannot see ANY folders.")
            logger.warning("Please verify you have shared the folder with the email above.")
        else:
            logger.info(f"Found {len(items)} folders:")
            for item in items:
                logger.info(f" - Name: {item['name']} | ID: {item['id']} | Parents: {item.get('parents')}")
                
        # 3. Check specific ID from .env
        target_id = os.environ.get('CV_TO_JSON_FOLDER_ID')
        if target_id:
            logger.info(f"\nChecking specific ID from .env: {target_id}")
            try:
                f = service.files().get(fileId=target_id, fields="id, name, capabilities").execute()
                logger.info(f"SUCCESS! Found folder: {f['name']}")
                logger.info(f"Capabilities: {f.get('capabilities')}")
            except Exception as e:
                logger.error(f"FAILED to access target folder: {e}")
                logger.error("Reason: The robot does not have permission to view this specific folder ID.")

    except Exception as e:
        logger.error(f"Fatal Error: {e}")

if __name__ == "__main__":
    debug_permissions()
