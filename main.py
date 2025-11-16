import os
import json
from datetime import datetime
from google_drive import get_drive_service, download_files_from_folder, get_or_create_folder, upload_file_to_folder
from parsers import parse_cv
from formatters import generate_pdf_from_data

# Configuration
DOWNLOADS_DIR = "downloaded_cvs"
OUTPUTS_DIR = "processed_cvs"
TEMPLATE_PATH = "templates/template.html"

def main():
    """
    Script principal pour le traitement des CV depuis Google Drive.
    """
    print("--- Début du traitement des CV ---")

    # Récupération des variables d'environnement
    source_folder_id = os.environ.get('SOURCE_FOLDER_ID')
    destination_folder_name = os.environ.get('DESTINATION_FOLDER_NAME', 'CV-Processed')

    if not source_folder_id:
        print("Erreur : La variable d'environnement SOURCE_FOLDER_ID n'est pas définie.")
        return

    # Authentification et service Drive
    try:
        drive_service = get_drive_service()
    except Exception as e:
        print(f"Erreur lors de l'authentification à Google Drive : {e}")
        return

    # Log du dossier source
    print(f"ID du dossier source Google Drive : {source_folder_id}")

    # Téléchargement des CV
    print(f"Téléchargement des fichiers depuis le dossier source...")
    downloaded_files = download_files_from_folder(drive_service, source_folder_id, DOWNLOADS_DIR)

    if not downloaded_files:
        print("Aucun fichier à traiter. Fin du script.")
        return

    # Création ou récupération du dossier de destination racine sur Google Drive
    print(f"Vérification du dossier de destination racine : '{destination_folder_name}'")
    dest_folder_id = get_or_create_folder(drive_service, destination_folder_name)
    print(f"ID du dossier de destination racine Google Drive : {dest_folder_id}")

    # Création d'un sous-dossier horodaté pour cette exécution
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    sub_folder_id = get_or_create_folder(drive_service, timestamp, parent_id=dest_folder_id)
    print(f"ID du sous-dossier de sortie pour cette exécution : {sub_folder_id}")

    # Création du dossier de sortie local
    os.makedirs(OUTPUTS_DIR, exist_ok=True)

    # Traitement de chaque fichier
    for file_path in downloaded_files:
        filename = os.path.basename(file_path)
        base_name = os.path.splitext(filename)[0]
        print(f"\n--- Traitement de : {filename} ---")

        # Analyse du CV
        parsed_data = parse_cv(file_path)

        if parsed_data:
            # 1. Enregistrer le JSON
            json_output_path = os.path.join(OUTPUTS_DIR, f"{base_name}_processed.json")
            # Convertir les types de données non sérialisables (ex: numpy.int64) en int
            if 'no_of_pages' in parsed_data:
                parsed_data['no_of_pages'] = int(parsed_data['no_of_pages'])
            
            with open(json_output_path, 'w', encoding='utf-8') as f:
                json.dump(parsed_data, f, ensure_ascii=False, indent=4)
            print(f"Données extraites enregistrées dans : {json_output_path}")

            # 2. Générer le PDF formaté
            pdf_output_path = os.path.join(OUTPUTS_DIR, f"{base_name}_processed.pdf")
            generate_pdf_from_data(parsed_data, TEMPLATE_PATH, pdf_output_path)
            
            # 3. Uploader les fichiers générés sur Google Drive
            print("Upload des résultats sur Google Drive...")
            upload_file_to_folder(drive_service, json_output_path, sub_folder_id)
            upload_file_to_folder(drive_service, pdf_output_path, sub_folder_id)
        else:
            print(f"Impossible d'analyser le CV : {filename}. Fichier ignoré.")

    print("\n--- Traitement terminé pour tous les fichiers. ---")


if __name__ == "__main__":
    main()
