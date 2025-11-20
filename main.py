import os
import json
from concurrent.futures import ThreadPoolExecutor
from google_drive import get_drive_service, download_files_from_folder, upload_file_to_folder
from parsers import parse_cv, load_spacy_model
from formatters import generate_pdf_from_data

# Configuration
DOWNLOADS_DIR = "downloaded_cvs"
OUTPUTS_DIR = "processed_cvs"
TEMPLATE_PATH = "templates/template.html"
MAX_WORKERS = 4  # Nombre de threads parallèles (ajuster selon les ressources du runner)

def process_single_file(file_path, drive_service, source_folder_id):
    """
    Traite un fichier unique : Parsing -> JSON -> PDF -> Upload.
    Cette fonction est exécutée par les threads.
    """
    filename = os.path.basename(file_path)
    base_name = os.path.splitext(filename)[0]
    print(f"--- Début traitement : {filename} ---")

    try:
        # 1. Analyse du CV
        parsed_data = parse_cv(file_path)

        if parsed_data:
            # 2. Enregistrer le JSON
            json_output_path = os.path.join(OUTPUTS_DIR, f"{base_name}_processed.json")
            
            with open(json_output_path, 'w', encoding='utf-8') as f:
                json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            
            # 3. Générer le PDF formaté
            pdf_output_path = os.path.join(OUTPUTS_DIR, f"{base_name}_processed.pdf")
            generate_pdf_from_data(parsed_data, TEMPLATE_PATH, pdf_output_path)
            
            # 4. Uploader les fichiers générés sur Google Drive
            print(f"Upload des résultats pour {filename}...")
            upload_file_to_folder(drive_service, json_output_path, source_folder_id)
            upload_file_to_folder(drive_service, pdf_output_path, source_folder_id)
            
            print(f"--- Succès traitement : {filename} ---")
            return True
        else:
            print(f"--- Échec parsing : {filename} ---")
            return False

    except Exception as e:
        print(f"!!! ERREUR CRITIQUE sur {filename} : {e}")
        return False

def main():
    """
    Script principal pour le traitement des CV depuis Google Drive.
    """
    print("--- Début du pipeline ETL CV ---")

    # Récupération des variables d'environnement
    source_folder_id = os.environ.get('SOURCE_FOLDER_ID')

    if not source_folder_id:
        print("Erreur : La variable d'environnement SOURCE_FOLDER_ID n'est pas définie.")
        return

    # Authentification et service Drive
    try:
        drive_service = get_drive_service()
    except Exception as e:
        print(f"Erreur lors de l'authentification à Google Drive : {e}")
        return

    # Préchauffage du modèle NLP (une seule fois pour tous les threads)
    print("Chargement du modèle NLP...")
    load_spacy_model()

    # Téléchargement des CV
    print(f"Téléchargement des fichiers depuis le dossier source...")
    downloaded_files = download_files_from_folder(drive_service, source_folder_id, DOWNLOADS_DIR)

    if not downloaded_files:
        print("Aucun fichier à traiter. Fin du script.")
        return

    # Création du dossier de sortie local
    os.makedirs(OUTPUTS_DIR, exist_ok=True)

    # Traitement parallèle
    print(f"Lancement du traitement parallèle avec {MAX_WORKERS} workers...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # On passe le service et l'ID dossier à chaque tâche
        # Note: Le client Google API est thread-safe pour la plupart des opérations,
        # ou gère ses propres connexions poolées.
        futures = [
            executor.submit(process_single_file, file_path, drive_service, source_folder_id)
            for file_path in downloaded_files
        ]
        
        # Attente de la fin de toutes les tâches
        for future in futures:
            future.result()

    print("\n--- Pipeline terminé. ---")

if __name__ == "__main__":
    main()
