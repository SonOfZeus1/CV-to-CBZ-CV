import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv # Import load_dotenv

from google_drive import get_drive_service, download_files_from_folder, upload_file_to_folder
from parsers import parse_cv
from formatters import generate_pdf_from_data

# --- Configuration Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration
DOWNLOADS_DIR = "downloaded_cvs"
OUTPUTS_DIR = "processed_cvs"
TEMPLATE_PATH = "templates/template.html"
# Réduction de la concurrence pour stabilité (évite SSL error & Segfault)
MAX_WORKERS = 1 
BLUR_CONTACT_INFO = True # Set to True to blur name, email, and phone for anonymous CVs 

def process_single_file(file_path, drive_service, source_folder_id):
    """
    Traite un fichier unique : Parsing -> JSON -> PDF -> Upload.
    """
    filename = os.path.basename(file_path)
    
    # --- SÉCURITÉ ANTI-BOUCLE ---
    # Si jamais le fichier a réussi à passer le filtre Drive, on le bloque ici.
    if "_processed" in filename:
        logger.warning(f"SKIP: Fichier déjà traité détecté (sécurité interne) : {filename}")
        return False
        
    base_name = os.path.splitext(filename)[0]
    
    # Check if output already exists locally to avoid re-processing
    json_output_path = os.path.join(OUTPUTS_DIR, f"{base_name}_processed.json")
    pdf_output_path = os.path.join(OUTPUTS_DIR, f"{base_name}_processed.pdf")
    
    if os.path.exists(json_output_path) and os.path.exists(pdf_output_path):
        logger.info(f"SKIP: Fichiers de sortie déjà existants localement pour {filename}")
        return True

    logger.info(f"START Traitement : {filename}")

    try:
        # 1. Analyse du CV
        logger.info(f"Parsing du fichier {filename}...")
        parsed_data = parse_cv(file_path)

        if parsed_data:
            # 2. Enregistrer le JSON
            with open(json_output_path, 'w', encoding='utf-8') as f:
                json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
            # 3. Générer le PDF formaté
            generate_pdf_from_data(parsed_data, TEMPLATE_PATH, pdf_output_path, blur_contact=BLUR_CONTACT_INFO)
            
            # 4. Uploader les fichiers générés sur Google Drive
            logger.info(f"Upload des résultats pour {filename}...")
            
            # Vérification anti-doublon avant upload
            # On liste les fichiers existants avec le même nom dans le dossier cible
            existing_files = []
            try:
                 q = f"'{source_folder_id}' in parents and name = '{os.path.basename(json_output_path)}' and trashed = false"
                 res = drive_service.files().list(q=q, fields="files(id)").execute()
                 existing_files = res.get('files', [])
            except Exception:
                 pass

            if not existing_files:
                upload_file_to_folder(drive_service, json_output_path, source_folder_id)
            else:
                logger.info(f"Fichier JSON déjà présent sur Drive, skip upload : {json_output_path}")

            existing_pdfs = []
            try:
                 q = f"'{source_folder_id}' in parents and name = '{os.path.basename(pdf_output_path)}' and trashed = false"
                 res = drive_service.files().list(q=q, fields="files(id)").execute()
                 existing_pdfs = res.get('files', [])
            except Exception:
                 pass

            if not existing_pdfs:
                upload_file_to_folder(drive_service, pdf_output_path, source_folder_id)
            else:
                logger.info(f"Fichier PDF déjà présent sur Drive, skip upload : {pdf_output_path}")
            
            logger.info(f"SUCCESS : {filename}")
            return True
        else:
            logger.warning(f"FAILURE Parsing (Données vides) : {filename}")
            return False

    except Exception as e:
        logger.error(f"CRITICAL ERROR sur {filename} : {e}", exc_info=True)
        return False

def main():
    """
    Script principal pour le traitement des CV depuis Google Drive.
    """
    # Load environment variables
    load_dotenv()
    
    logger.info("--- Début du pipeline ETL CV ---")

    # Récupération des variables d'environnement
    source_folder_id = os.environ.get('SOURCE_FOLDER_ID')

    if not source_folder_id:
        logger.error("La variable d'environnement SOURCE_FOLDER_ID n'est pas définie.")
        return

    # Authentification et service Drive
    try:
        drive_service = get_drive_service()
    except Exception as e:
        logger.critical(f"Erreur authentification Google Drive : {e}")
        return

    # Téléchargement des CV
    logger.info(f"Téléchargement des fichiers depuis le dossier source...")
    # Note: download_files_from_folder inclut maintenant un filtre "not name contains '_processed'"
    downloaded_files = download_files_from_folder(drive_service, source_folder_id, DOWNLOADS_DIR)

    if not downloaded_files:
        logger.info("Aucun fichier à traiter. Fin du script.")
        return
        
    # Deduplicate files based on filename to avoid double processing
    downloaded_files = list(set(downloaded_files))
    logger.info(f"{len(downloaded_files)} fichiers uniques à traiter.")

    # Création du dossier de sortie local
    os.makedirs(OUTPUTS_DIR, exist_ok=True)

    # Traitement parallèle (désactivé pour stabilité = 1)
    logger.info(f"Lancement du traitement avec {MAX_WORKERS} workers...")
    
    # Utilisation de ThreadPoolExecutor même avec 1 worker pour garder la structure
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(process_single_file, file_path, drive_service, source_folder_id)
            for file_path in downloaded_files
        ]
        
        for future in futures:
            # On attend le résultat pour propager les exceptions si besoin
            try:
                future.result()
            except Exception as e:
                logger.error(f"Erreur dans un thread : {e}")

    logger.info("--- Pipeline terminé. ---")

if __name__ == "__main__":
    main()
